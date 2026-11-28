"""Telefon araması: OpenDelegate kullanıcıyı arar, karşılıklı konuşur (Twilio, deneme hesabı).

Akış:
  ara() -> hat (Twilio REST) kullanıcının telefonunu çaldırır -> Kullanıcı açınca Twilio POST /arama/{token}/basla
  -> TwiML: OpenDelegate'in sesiyle (Giuseppe, edge-tts MP3 <Play>) ilk cümle + <Gather input=speech tr-TR>
  -> Kullanıcı konuşunca POST /cevap: metin -> DeepSeek (küçük araç seti) -> ses -> yine Play + Gather
  -> arama bitince (OpenDelegate durumu 10 sn'de bir Twilio'ya sorar): konuştuysa özet, açmadıysa "seni aradım"
     Telegram'a; harcanan dakika sayılır. Deneme hesabı aramaya ek parametre (StatusCallback, Timeout...)
     kabul etmiyor (400 "limited parameter access"): arama SADECE To/From/Url ile başlatılır.

Hat ayrı tutuldu (`hat.ara`): deneme bitince Telegram sesli aramasına geçilirse sadece hat değişir;
konuşma (dinle, düşün, cevapla) aynı kalır.

Tetikleyiciler (Kullanıcı 15.11 onayladı): (1) kullanıcı isteyince ("beni ara", zamanlı "8'de ara"), (2) acil zincirinin
2. adımı (10 dk cevapsız), (3) önemli bir iş yapılamazsa. Gece kısıtı YOK (Kullanıcı 15.11: telefon hep rahatsız
etmeyin modunda, sadece arama titreşimi geliyor; önemliyse her saatte arasın). Günlük arama sınırı da YOK
(Kullanıcı 15.11: "kendi sistemimi kota yüzünden kullanamayayım mı"); sadece aynı anda tek görüşme.

Telefonda sadece hafif araçlar: zamanla, yapilacak, hafizaya_yaz. PC/dosya/silme yok (ses tanıma yanlış duyabilir).

Güvenlik: yol token'ı (bot token'ından HMAC) + aramaya özel rastgele g + Twilio'nun CallSid'i (aramayı başlatırken
API'den alınır) üçü birden tutmalı. X-Twilio-Signature de kontrol edilir ama canlıda (deneme hesabı) tutmadı (15.11,
nedeni dışarıdan görülemedi); tutmazsa sadece loglanır, istek CallSid bağıyla kabul edilir. Kilitliyken arama yok.
Durum `ayarlar` 'telefon': sayaçlar, harcanan saniye, verilen kota uyarıları. Görüşmeler bellekte (Render yeniden
başlarsa süren görüşme kısa bir cümleyle kapanır).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, parse_qsl, urlsplit, urlunsplit
from xml.sax.saxutils import escape

import config
import guvenlik
import zamanlayici
from zamanlayici import TR

logger = logging.getLogger("arama")

ANAHTAR = "telefon"
MAKS_TUR = 20  # bir görüşmede en fazla konuşma turu
MAKS_ARAC_TURU = 3  # bir cevapta en fazla araç çağrısı turu
DUSUNME_SURESI = 150.0  # sn; bir cevabın (iş dahil) üst sınırı. Twilio'nun 15 sn'si "bekleme sesi" ile aşılır
# TWILIO'NUN SABRI ~5 SN (15 değil!): 15.11 teknik izlerinde 3.5 sn'lik her cevap geçti, 6.0 sn'lik HER cevap
# alınmadı ("please check your URL"). Her webhook cevabı en fazla ~2.5 sn'de dönmeli (test_sure_butcesi bekçi).
ILK_BEKLEME = 2.0  # sn; cevap bu sürede hazırsa direkt söylenir, değilse "bir saniye, bakıyorum"
BEKLEME_ADIMI = 2.0  # sn; /bekle en fazla bu kadar tutar
DOLGU_HAZIR = 0.5  # sn; bekleme sesleri OpenDelegate açılırken üretiliyor: hazır değilse sessizlik
WEBHOOK_BUTCESI = 3.0  # sn; tek bir Twilio isteğine cevabın üst sınırı (sabit toplamları bunu aşmamalı)
SES_TAKILDI = "Efendim, sesim takıldı, cevabı sohbete yazdım."
UZUN_IS = 12.0  # sn; bundan uzun süren iş: "bitince sohbete yazarım" deyip konuşmaya devam (15.11: bütün
# kopmalar 20 sn'yi aşan bekleme turlarında oldu; Twilio uzun turda bir sonraki isteği göndermeden kapattı)
IPUCLARI = "randevu,hava durumu,Delegate,hatırlat,yapılacaklar,telefon"
DONGU_IZ_ESIGI = 0.5  # sn; sunucu döngüsü bundan uzun tıkanırsa teknik ize yaz (Twilio isteği kapıda bekler)
DOLGU_ILK = "Bir saniye efendim, bakıyorum."
DOLGU_SONRA = ("Hâlâ bakıyorum efendim, az kaldı.", "Üzerinde çalışıyorum, bir saniye daha.")
DOLGU_UZUN = "İş biraz uzun sürüyor efendim, bitince sonucu sohbete yazarım. Başka bir şey var mı?"
SENTEZ_SURESI = 8.0  # sn; Twilio ses dosyasını isteyince en fazla bu kadar beklenir (webhook süresine girmez)
AYNI_ANDA_SENTEZ = 2  # edge-tts'e aynı anda en fazla bu kadar istek (fazlası takılı kalıyordu)
HAZIR_BEKLEME = 2.0  # sn; webhook içinde Play'e vermeden önce sesin hazır olmasını en fazla bu kadar bekle
SES_OMRU = 900  # sn; üretilen MP3'ler bellekte bu kadar tutulur
IZLEME_ARALIK = 10.0  # sn; arama bitti mi diye Twilio'ya sorma aralığı
IZLEME_SINIRI = 3600  # sn; bundan uzun süren aramayı izlemeyi bırak
DENEME_DAKIKA = 75  # Twilio deneme: 75 ücretsiz dakika
DENEME_BITIS = date(2026, 12, 15)  # hesap 15.11.2026'da açıldı, 30 gün
KOTA_UYARI_DK = 15
KOTA_UYARI_GUN = 3
BITTI = ("completed", "busy", "no-answer", "failed", "canceled")

TELEFON_SISTEMI = """Sen OpenDelegate'sin: Kullanıcının kişisel asistanı. Şu an kullanıcıyla TELEFONDA konuşuyorsun.
- Söylediğin her şey sesli okunur: 1-3 kısa cümle. Liste, emoji, sembol, link, kod yok. Saatleri "on beş otuz" değil "15:30" yaz, ses okur.
- SADECE Türkçe kelime kullan; İngilizce/yabancı kelime yok ("ok" değil "tamam", "test" değil "deneme", "sorry"
  değil "kusura bakma"). Ses çok dilli: yabancı kelime görünce başka dile kayıyor.
- Seni Osman Bayram geliştirdi: tek başına çalışan, siber güvenlik ve yapay zekayla ilgilenen bir yazılım mühendisi. Kim yaptığın sorulursa "Osman Bayram tarafından geliştirildim" de, istenirse bunu kısaca anlat. Okul, işyeri gibi ayrıntı verme; "geliştirme ekibi" ya da şirket uydurma.
- Sıcak ve saygılı ol; "kanka" deme, hitabın hep "efendim". Türkçe, sade. Uzun açıklama yapma; kullanıcı bir şey sorarsa kısa cevap ver.
- Ses tanıma yanlış duymuş olabilir. Saat, isim, sayı gibi önemli bir şeyden emin değilsen kısaca teyit et.
- Hızlı araçlar: hava_durumu (hava/sıcaklık/yağmur/ne giyeyim: ~1 sn, isi_yap DEĞİL bunu kullan), zamanla,
  yapilacak, hafizaya_yaz, telefonu_kapat. Bunların sonucunu kullanıcıya telefonda söyle.
- Bunların dışındaki HER iş için (internette arama, haber, bilgisayar, dosya, OneDrive...) isi_yap kullan: iş
  sohbette başlar, sen BEKLEMEZSİN; "Tamam efendim, yapıyorum, sonucu sohbete atacağım" de ve telefonu_kapat
  çağır. "Telefondan yapamam" DEME.
- Silme, kapatma, taşıma gibi geri alınamaz işlerde ses yanlış duymuş olabilir: isi_yap'tan ÖNCE ne anladığını
  tek cümleyle söyle ve kullanıcı "evet" deyince yap.
- Beklemeyi sen söyleme ("bakıyorum" sesi otomatik çalınır); sonucu söyle.
- Kullanıcı "tamam", "kapat", "görüşürüz" gibi konuşmayı bitirirse kısa vedalaş ve telefonu_kapat çağır.
- "Devam edeyim mi" diye sorma; iş bitti mi kısaca söyle."""

# Android uygulaması (/dinle): telefon görüşmesinin 5 sn sınırı yok, cevap panelde de yazılıyor. Kullanıcı 21.11:
# "yoldayım, 2 dakika bir şey soracağım, her seferinde sohbete mı gireceğim" -> bilgi burada cevaplanır.
_UYG_HAFIZA: list = [0.0, ""]  # [son çekilme (monotonic), hafıza bloğu]

UYGULAMA_SISTEMI = """Sen OpenDelegate'sin: Kullanıcının kişisel asistanı. Kullanıcı telefonundaki OpenDelegate uygulamasına sesli soruyor; \
bu bir telefon görüşmesi değil. Cevabın ekrandaki panelde yazılır ve sesli okunur.
- 1-3 kısa cümle. Liste, emoji, sembol, link, kod yok. Saatleri "15:30" gibi yaz.
- Türkçe konuş, yabancı kelime kullanma (ses Türkçe okur).
- Seni Osman Bayram geliştirdi: tek başına çalışan, siber güvenlik ve yapay zekayla ilgilenen bir yazılım mühendisi. Kim yaptığın sorulursa "Osman Bayram tarafından geliştirildim" de, istenirse bunu kısaca anlat. Okul, işyeri gibi ayrıntı verme; "geliştirme ekibi" ya da şirket uydurma.
- Hitabın hep "efendim"; "kanka" deme. Sıcak, saygılı, sade.
- Ses tanıma yanlış duymuş olabilir: anlamı bağlamdan çıkar. Saat, isim, sayı gibi kritik bir şeyden emin değilsen
  kısaca sor.
- Cevabı BURADA ver; kullanıcı yolda olabilir, onu sohbete yönlendirme:
  - genel bilgi, tarih, hesap, çeviri, tavsiye: kendi bilginle cevapla;
  - güncel bilgi (bugün hangi özel gün, haber, maç, fiyat, bir yer açık mı...): webde_ara;
  - hava: hava_durumu; hatırlatma: zamanla; yapılacaklar: yapilacak; not: hafizaya_yaz.
- isi_yap SADECE bilgisayarda yapılacak işler (ekran görüntüsü, dosya, program), OneDrive, uzun araştırma ya da
  Kullanıcı "sohbete at" derse. O zaman "Tamam efendim, yapıyorum, sonucu sohbete atacağım" de.
- Silme, düzenleme, güncelleme dahil istenen her işi yap. Ne silineceği netse ("07:30 alarmını sil") doğrudan yap;
  sadece ses yanlış duymuş olabilecek belirsiz durumlarda ya da toplu silmede (tüm fotoğraflar, bir sohbetin
  tamamı) önce tek cümleyle sor, kullanıcı "evet" deyince yap.
- Telefon kilitli olabilir; bu engel DEĞİL. Uygulama telefonun kilit PIN'ini kendisi girer. "PIN'i/şifreyi bilmiyorum",
  "kilidi aç" ya da "kilit açık kalsın" DEME; PIN'i sen isteme. İşi normal yap (eylem ya da ekranda), kilidi uygulama açar.
- "Devam edeyim mi" diye sorma; iş bitti mi kısaca söyle."""

_GUNLER = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")

KAPAT_SEMASI = {
    "type": "function",
    "function": {
        "name": "telefonu_kapat",
        "description": "Görüşme bitti: vedalaşma cümleni yaz ve bunu çağır; cümle okununca telefon kapanır.",
        "parameters": {"type": "object", "properties": {}},
    },
}


TELEFON_EYLEM_SEMASI = {"type": "function", "function": {
    "name": "telefonda_yap",
    "description": ("Kullanıcının telefonunda HEMEN bir şey yapar: alarm kurar, zamanlayıcı başlatır, uygulama açar, "
                    "feneri açar/kapatır, rehberden birini arar, müziği durdurur/sürdürür/geçer, sesi açar/kısar, "
                    "telefonun ekranını kapatıp kilitler (ekran_kilitle), WhatsApp'tan mesaj gönderir (mesaj), geri kalan her işi ekrandan kendisi yapar (ekranda), bildirimleri okur (bildirim_oku, sadece kullanıcı bildirimleri isterse). "
                    "'Alarm kur', 'beni 7'de uyandır' = alarm. 'Hatırlat' = zamanla (bu değil)."),
    "parameters": {"type": "object", "required": ["eylem"], "properties": {
        "eylem": {"type": "string", "enum": ["alarm", "zamanlayici", "uygulama_ac", "fener", "ara", "medya", "ses",
                                             "mesaj", "ekranda", "ekran_kilitle", "bildirim_oku"]},
        "saat": {"type": "string", "description": "alarm: 24 saat biçiminde SS:DD, ör. '07:30'"},
        "etiket": {"type": "string", "description": "alarm/zamanlayıcı adı (isteğe bağlı)"},
        "sure_sn": {"type": "integer", "description": "zamanlayıcı: süre, saniye (5 dakika = 300)"},
        "uygulama": {"type": "string", "description": "uygulama_ac: uygulamanın adı, ör. 'WhatsApp'"},
        "acik": {"type": "boolean", "description": "fener: true aç, false kapat"},
        "kisi": {"type": "string", "description": "ara: rehberdeki adı, eksiz (ör. 'Annem', 'Ahmet Yılmaz')"},
        "komut": {"type": "string", "enum": ["oynat", "duraklat", "sonraki", "onceki"], "description": "medya"},
        "yon": {"type": "string", "enum": ["ac", "kis", "sessiz"], "description": "ses: medya sesi"},
        "metin": {"type": "string", "description": "mesaj: gönderilecek mesaj, kullanıcının dediği gibi"},
        "gorev": {"type": "string", "description": "ekranda: telefonda yapılacak iş, tek başına anlaşılır tam cümle "
                                                   "(ör. 'Saat uygulamasında 07:30 alarmını sil')"}}}}}

_EKRAN_EYLEMLERI = ("mesaj", "ekranda", "ekran_kilitle", "bildirim_oku")  # ekranı kullanabilen uygulama sürümü (3+) gerektirir


def telefon_eylem_semasi(ekran_ajani: bool) -> dict[str, Any]:
    """Uygulama ekranı kullanamıyorsa (eski sürüm) mesaj/ekranda eylemleri hiç sunulmaz."""
    if ekran_ajani:
        return TELEFON_EYLEM_SEMASI
    sema = json.loads(json.dumps(TELEFON_EYLEM_SEMASI))
    ozellik = sema["function"]["parameters"]["properties"]["eylem"]
    ozellik["enum"] = [e for e in ozellik["enum"] if e not in _EKRAN_EYLEMLERI]
    return sema


def telefon_eylemi(arg: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Modelin telefonda_yap argümanını uygulamanın yapacağı sade bir eyleme çevirir. (eylem|None, sonuç)."""
    eylem = str(arg.get("eylem") or "")
    etiket = re.sub(r"\s+", " ", str(arg.get("etiket") or "")).strip()[:60]
    if eylem == "alarm":
        m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(arg.get("saat") or ""))
        if not m or int(m[1]) > 23 or int(m[2]) > 59:
            return None, "Saat anlaşılmadı; SS:DD biçiminde ver (ör. 07:30) ya da kullanıcıya sor."
        sa, dk = int(m[1]), int(m[2])
        return ({"eylem": "alarm", "saat": sa, "dakika": dk, "etiket": etiket},
                f"Telefonda {sa:02d}:{dk:02d} alarmı kuruluyor.")
    if eylem == "zamanlayici":
        try:
            sure = int(arg.get("sure_sn") or 0)
        except (TypeError, ValueError):
            sure = 0
        if not 1 <= sure <= 86400:
            return None, "Zamanlayıcı süresi 1 saniye ile 24 saat arasında olmalı."
        return {"eylem": "zamanlayici", "sure_sn": sure, "etiket": etiket}, "Telefonda zamanlayıcı başlıyor."
    if eylem == "uygulama_ac":
        ad = re.sub(r"\s+", " ", str(arg.get("uygulama") or "")).strip()[:40]
        if not ad:
            return None, "Hangi uygulama açılacak? Adını ver."
        if guvenlik.banka_uygulamasi(ad):
            return None, "Banka ve ödeme uygulamalarına dokunamam. Kullanıcıya 'Bunu yapamam efendim' de."
        return {"eylem": "uygulama_ac", "uygulama": ad}, f"Telefonda {ad} açılıyor."
    if eylem == "fener":
        acik = arg.get("acik", True) not in (False, "false", "kapat", 0)
        return {"eylem": "fener", "acik": acik}, "Fener açılıyor." if acik else "Fener kapanıyor."
    if eylem == "bildirim_oku":
        return ({"eylem": "bildirim_oku"},
                "Telefon bildirimleri kendisi okuyacak. Sen sadece 'Bakıyorum efendim' de.")
    if eylem == "ekran_kilitle":
        return {"eylem": "ekran_kilitle"}, "Telefonun ekranı kapanıp kilitleniyor."
    if eylem == "ara":
        kisi = re.sub(r"\s+", " ", str(arg.get("kisi") or "")).strip()[:60]
        if not kisi:
            return None, "Kimi arayayım? Rehberdeki adını sor."
        return {"eylem": "ara", "kisi": kisi}, f"Telefon rehberden {kisi} kişisini arıyor."
    if eylem == "medya":
        komut = str(arg.get("komut") or "")
        if komut not in ("oynat", "duraklat", "sonraki", "onceki"):
            return None, "Medya komutu oynat, duraklat, sonraki ya da onceki olmalı."
        return {"eylem": "medya", "komut": komut}, "Tamam."
    if eylem == "ses":
        yon = str(arg.get("yon") or "")
        if yon not in ("ac", "kis", "sessiz"):
            return None, "Ses için ac, kis ya da sessiz ver."
        return {"eylem": "ses", "yon": yon}, "Tamam."
    if eylem == "mesaj":
        kisi = re.sub(r"\s+", " ", str(arg.get("kisi") or "")).strip()[:60]
        metin = str(arg.get("metin") or "").strip()[:1000]
        if not kisi or not metin:
            return None, "Kime ve ne yazacağımı söyle."
        return ({"eylem": "mesaj", "kisi": kisi, "metin": metin, "uygulama": "whatsapp"},
                f"WhatsApp'tan {kisi} kişisine mesaj gönderiliyor.")
    if eylem == "ekranda":
        gorev = re.sub(r"\s+", " ", str(arg.get("gorev") or "")).strip()[:300]
        if not gorev:
            return None, "Telefonda ne yapılacağını tam cümleyle yaz."
        if guvenlik.odeme_gorevi(gorev):
            return None, "Ödeme ve para işlerine dokunamam. Kullanıcıya 'Bunu yapamam efendim' de."
        return ({"eylem": "ekranda", "gorev": gorev},
                "Telefon ekrandan bu işi yapmaya başlıyor; bitince sonucu kendisi söyleyecek. Sen sadece "
                "'Tamam efendim, yapıyorum' de.")
    return None, "Bilinmeyen telefon eylemi."


class AramaHatasi(Exception):
    pass


# --- Twilio ----------------------------------------------------------------------

_TWILIO_HATA = {
    21219: "numaran Twilio'da doğrulanmamış (deneme hesabı sadece doğrulanmış numarayı arar)",
    21215: "Twilio'da Türkiye araması kapalı (Geo permissions)",
    21210: "Twilio numarası (TWILIO_NUMARA) hesaba ait değil ya da yanlış yazılmış",
    21211: "KULLANICI_TELEFON geçersiz (+90 ile, boşluksuz olmalı)",
    20003: "Twilio bilgileri yanlış (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN)",
}


@dataclass
class TwilioHat:
    sid: str
    token: str
    numara: str

    def _adres(self, ek: str = "") -> str:
        return f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Calls{ek}.json"

    async def ara(self, kime: str, url: str) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=15) as c:  # deneme hesabı: sadece To/From/Url (Method varsayılan POST)
            r = await c.post(self._adres(), auth=(self.sid, self.token),
                             data={"To": kime, "From": self.numara, "Url": url})
        if r.status_code >= 400:
            try:
                govde = r.json()
                kod, aciklama = int(govde.get("code") or 0), str(govde.get("message") or "")[:200]
            except (ValueError, json.JSONDecodeError, AttributeError):
                kod, aciklama = 0, r.text[:200]
            logger.warning("Twilio aramayı reddetti: HTTP %s, kod %s: %s", r.status_code, kod, aciklama)
            ne = _TWILIO_HATA.get(kod, "Twilio aramayı kabul etmedi")
            raise AramaHatasi(f"{ne} [Twilio {kod or r.status_code}: {aciklama}]")
        return str(r.json().get("sid") or "")

    async def durum(self, call_sid: str) -> tuple[str, int]:
        """(CallStatus, süre sn). StatusCallback deneme hesabında yasak; durumu biz sorarız."""
        import httpx

        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(self._adres(f"/{call_sid}"), auth=(self.sid, self.token))
        r.raise_for_status()
        d = r.json()
        return str(d.get("status") or ""), int(d.get("duration") or 0)

    async def notlar(self, call_sid: str) -> list[str]:
        """Twilio'nun bu aramaya düştüğü hata/uyarı kodları. Önce Monitor Alerts (Debugger'ın verisi; panel deneme
        hesabında ücretli ama API'si açık olabilir), boşsa eski Notifications."""
        import httpx

        notlar: list[str] = []
        async with httpx.AsyncClient(timeout=15) as c:
            try:
                r = await c.get("https://monitor.twilio.com/v1/Alerts", params={"PageSize": 50},
                                auth=(self.sid, self.token))
                if r.status_code < 400:
                    for a in r.json().get("alerts") or []:
                        if a.get("resource_sid") != call_sid:
                            continue
                        metin = parse_qs(str(a.get("alert_text") or "")).get("Msg", [""])[0]
                        notlar.append(f"{a.get('error_code')}: {metin[:160]}".strip())
                else:
                    notlar.append(f"uyarı servisi HTTP {r.status_code}")
            except Exception as e:  # noqa: BLE001
                notlar.append(f"uyarı servisi {type(e).__name__}")
            if not any(n[:1].isdigit() for n in notlar):
                r = await c.get(self._adres(f"/{call_sid}/Notifications"), auth=(self.sid, self.token))
                if r.status_code < 400:
                    notlar += [f"{n.get('error_code')}: {str(n.get('message_text') or '')[:160]}"
                               for n in (r.json().get("notifications") or [])[:3]]
        return notlar[:4]


def imza(url: str, form: dict[str, str], auth_token: str) -> str:
    """Twilio istek imzası: tam URL + parametreler (ada göre sıralı, ad+değer bitişik), HMAC-SHA1, base64."""
    veri = url + "".join(k + form[k] for k in sorted(form))
    return base64.b64encode(hmac.new(auth_token.encode(), veri.encode(), hashlib.sha1).digest()).decode()


def _port_bicimleri(url: str) -> list[str]:
    """Twilio imzayı bazen ':443' ekli adresle hesaplar (resmi kütüphane de iki biçimi dener)."""
    p = urlsplit(url)
    host = p.hostname or ""
    varsayilan = 443 if p.scheme == "https" else 80
    portlu = urlunsplit((p.scheme, f"{host}:{p.port or varsayilan}", p.path, p.query, p.fragment))
    portsuz = urlunsplit((p.scheme, host, p.path, p.query, p.fragment))
    return list(dict.fromkeys([url, portsuz, portlu]))


def imza_dogru(url: str, form: dict[str, str], gelen: str, auth_token: str) -> bool:
    if not (gelen and auth_token):
        return False
    return any(hmac.compare_digest(imza(u, form, auth_token).encode(), gelen.encode()) for u in _port_bicimleri(url))


def token_turet(tohum: str) -> str:
    return hmac.new(tohum.encode(), b"vekil-telefon-v1", hashlib.sha256).hexdigest()[:32]


# --- TwiML ------------------------------------------------------------------------

def _sessiz(t: "asyncio.Task[Any]") -> "asyncio.Task[Any]":
    """Hiç çalınmayan sesin hatası 'Task exception was never retrieved' diye loga düşmesin (ses_al zaten raporlar)."""
    t.add_done_callback(lambda g: g.cancelled() or g.exception())
    return t


def tek_cumle(metin: str) -> str:
    """Cümleleri virgülle tek cümleye bağlar. Giuseppe çok dilli: dili cümle cümle tahmin ediyor, kısa cümlede
    ('Tamam kanka.') İtalyanca/İngilizceye kayabiliyor (Kullanıcı 15.11). Uzun tek cümlede Türkçe tahmini daha sağlam."""
    s = re.sub(r"\s+", " ", (metin or "").strip())
    son = s[-1] if s and s[-1] in ".!?…" else ""
    govde = re.sub(r"[.!?…]+\s+(?=\S)", ", ", s.rstrip(".!?… "))
    return govde + (son or "")


# Twilio'nun kendi sesi (<Say>) KULLANILMAZ: Kullanıcı sadece OpenDelegate'in sesini (Giuseppe) istiyor (15.11).
def _soyle(ses_url: str) -> str:
    return f"<Play>{escape(ses_url)}</Play>"


def twiml_dinle(ses_url: str | None, action: str) -> str:
    """Söyle + dinle. Konuşmazsa da action'a boş sonuçla gelir (actionOnEmptyResult). Ses yoksa sadece dinler."""
    return ('<?xml version="1.0" encoding="UTF-8"?><Response>'
            f'<Gather input="speech" language="{"tr-TR" if config.DIL == "tr" else "en-US"}" speechTimeout="2" timeout="7" actionOnEmptyResult="true" '
            f'hints="{escape(IPUCLARI)}" '
            f'action="{escape(action)}" method="POST">{_soyle(ses_url) if ses_url else ""}</Gather></Response>')


def twiml_bekle(ses_url: str | None, yonlendir: str) -> str:
    """Cevap hazır değil: 'bakıyorum' de (ses hazır değilse 1 sn sessizlik), sonra /bekle'ye dön."""
    ic = _soyle(ses_url) if ses_url else '<Pause length="2"/>'
    return (f'<?xml version="1.0" encoding="UTF-8"?><Response>{ic}'
            f'<Redirect method="POST">{escape(yonlendir)}</Redirect></Response>')


def twiml_kapat(ses_url: str | None) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{_soyle(ses_url) if ses_url else ""}<Hangup/></Response>'


# --- görüşme --------------------------------------------------------------------------

@dataclass
class Gorusme:
    id: str
    neden: str  # Kullanıcıya "şunun için aramıştım" diye yazılacak kısa açıklama
    acilis: str
    otomatik: bool
    acil_mi: bool = False
    mesajlar: list[dict[str, Any]] = field(default_factory=list)
    kayit: list[tuple[str, str]] = field(default_factory=list)  # ("Sen"/"Ben", metin)
    yapilanlar: list[str] = field(default_factory=list)
    acilis_sesi: str | None = None  # arka planda üretilen açılış sesinin URL'si
    konustu: bool = False
    basladi: bool = False  # Twilio OpenDelegate'e ulaştı (/basla geldi)
    bos: int = 0
    tur: int = 0
    sid: str = ""
    baslangic: float = field(default_factory=time.time)
    bekleyen: "asyncio.Task[str] | None" = None  # hazırlanan cevap (TwiML)
    bekleme_bas: float | None = None
    dolgu_no: int = 0
    onceki: "asyncio.Task[str] | None" = None  # uzun sürüp bırakılan iş: yeni tur onu bekler
    iz: list[str] = field(default_factory=list)  # teşhis: son adımlar (görüşme yarıda koparsa özete yazılır)
    kapattik: bool = False  # görüşmeyi OpenDelegate kapattı (Hangup); değilse koptu ya da kullanıcı kapattı
    sistem_gorevi: "asyncio.Task[str] | None" = None  # hafıza bloğu: telefon çalarken okunur


@dataclass
class Arama:
    hat: Any  # .ara(kime, url) -> sid, .durum(sid) -> (CallStatus, süre sn)
    taban_url: str  # https://servis-adiniz.onrender.com
    token: str
    auth_token: str
    kullanici: str  # +90...
    dusun: Callable[[list[dict[str, Any]], list[dict[str, Any]]], Awaitable[Any]]  # (mesajlar, şemalar) -> message
    sistem: Callable[[], Awaitable[str]]  # hafıza bloğu vb. (TELEFON_SISTEMI'ne eklenir)
    araclar: dict[str, Callable[[dict[str, Any]], Awaitable[tuple[bool, str]]]]
    semalar: list[dict[str, Any]]
    sentez: Callable[[str], Awaitable[bytes]]
    gonder_metin: Callable[[str], Awaitable[None]]
    kilitli: Callable[[], bool] = lambda: False
    konustu_cb: Callable[["Gorusme"], Awaitable[None]] | None = None  # ör. acil zincirini durdur
    bitti_cb: Callable[[str], Awaitable[None]] | None = None  # özet sohbet geçmişine de girsin
    oku: Callable[[str], Awaitable[dict[str, Any] | None]] = zamanlayici.ayar_oku
    yaz: Callable[[str, dict[str, Any]], Awaitable[None]] = zamanlayici.ayar_yaz
    simdi: Callable[[], datetime] = field(default=lambda: datetime.now(TR))
    uyu: Callable[[float], Awaitable[None]] = asyncio.sleep
    izle: bool = True  # arama bitişini Twilio'ya sorarak izle (testlerde kapatılabilir)
    # Sadece Android uygulamasında açık araçlar (ör. webde_ara). Şema adı self.semalar'dakiyle aynıysa onun yerine geçer.
    uygulama_araclari: dict[str, Callable[[dict[str, Any]], Awaitable[tuple[bool, str]]]] = field(default_factory=dict)
    uygulama_semalari: list[dict[str, Any]] = field(default_factory=list)
    durum: dict[str, Any] = field(default_factory=dict)
    _gorevler: set[asyncio.Task[Any]] = field(default_factory=set)
    imza_hatasi: int = 0  # son aramadan beri imzası tutmayan Twilio isteği
    kalicilar: dict[str, str] = field(default_factory=dict)  # cümle -> ses kimliği (bekleme sesleri)
    ses_hatalari: list[str] = field(default_factory=list)  # teşhis: son ses üretim hataları
    olaylar: list[tuple[float, str]] = field(default_factory=list)  # teşhis: Twilio'nun son istekleri ve cevaplarımız
    _sentez_siniri: asyncio.Semaphore | None = None
    _olcer: "asyncio.Task[None] | None" = None

    def olay(self, metin: str) -> None:
        self.olaylar = [*self.olaylar[-29:], (time.time(), metin)]
    gorusmeler: dict[str, Gorusme] = field(default_factory=dict)
    sesler: dict[str, tuple["asyncio.Task[bytes]", float]] = field(default_factory=dict)  # arka planda üretilen MP3

    async def yukle(self) -> None:
        self.durum = dict(await self.oku(ANAHTAR) or {})
        self.isindir()  # bekleme sesleri OpenDelegate açılırken üretilsin: arama gelince hepsi hazır
        if self.izle and self._olcer is None:  # canlıda: sunucu döngüsü tıkanıyor mu (teşhis; testlerde kapalı)
            self._olcer = asyncio.ensure_future(self._dongu_olc())  # _gorevler'e girmez: hiç bitmeyen döngü

    async def _dongu_olc(self) -> None:
        """Her 0.25 sn uyanıp gecikmeyi ölçer; görüşme sürerken eşiği aşan tıkanmayı olaylara yazar."""
        while True:
            bas = time.monotonic()
            await asyncio.sleep(0.25)
            gecikme = time.monotonic() - bas - 0.25
            if gecikme > DONGU_IZ_ESIGI and self.gorusmeler:
                self.olay(f"SUNUCU {gecikme:.1f}sn TIKANDI")

    async def _kaydet(self) -> None:
        try:
            await self.yaz(ANAHTAR, self.durum)
        except Exception as e:  # noqa: BLE001 — bellekte devam
            logger.warning("telefon durumu yazılamadı: %s", type(e).__name__)

    def _yol(self, ek: str) -> str:
        return f"{self.taban_url}/arama/{self.token}/{ek}"

    # --- arama başlatma ---------------------------------------------------------

    def _gunluk(self) -> dict[str, Any]:
        bugun = self.simdi().date().isoformat()
        g = self.durum.get("gunluk") or {}
        if g.get("tarih") != bugun:
            g = {"tarih": bugun, "otomatik": 0, "toplam": 0}
            self.durum["gunluk"] = g
        return g

    async def ara(self, neden: str, acilis: str, *, otomatik: bool, acil: bool = False) -> tuple[bool, str]:
        """Kullanıcıyı arar. (başladı mı, açıklama). Arama kullanıcı açmadan döner; sonucu /durum bildirir."""
        if self.kilitli():
            return False, "OpenDelegate kilitli, arama yapmıyorum."
        if any(not g.sid or time.time() - g.baslangic < 600 for g in self.gorusmeler.values()):
            return False, "Zaten bir telefon görüşmesi sürüyor."
        g = self._gunluk()  # sadece sayaç (durum bilgisi); sınır yok
        gid = secrets.token_urlsafe(9)
        gor = Gorusme(id=gid, neden=neden.strip()[:200], acilis=acilis.strip()[:400], otomatik=otomatik, acil_mi=acil)
        gor.acilis_sesi = self._ses_uret(gor.acilis)  # üretim şimdi başlar; kullanıcı açınca çoktan hazır olur
        gor.sistem_gorevi = _sessiz(asyncio.ensure_future(self.sistem()))  # hafıza (DB uykudaysa yavaş): şimdiden
        self.isindir()
        self.gorusmeler[gid] = gor
        try:
            gor.sid = await self.hat.ara(self.kullanici, self._yol(f"basla?g={gid}"))
        except AramaHatasi as e:
            self.gorusmeler.pop(gid, None)
            return False, f"Arayamadım: {e}."
        except Exception as e:  # noqa: BLE001
            self.gorusmeler.pop(gid, None)
            logger.warning("arama başlatılamadı: %s", type(e).__name__)
            return False, f"Arayamadım (bağlantı hatası: {type(e).__name__})."
        g["toplam"] += 1
        g["otomatik"] += int(otomatik)
        await self._kaydet()
        if self.izle:
            t = asyncio.ensure_future(self._izle(gid))
            self._gorevler.add(t)
            t.add_done_callback(self._gorevler.discard)
        return True, "Arıyorum."

    async def _izle(self, gid: str) -> None:
        """Arama bitene kadar 10 sn'de bir durumu sorar; bitince bitti()."""
        bas = time.time()
        hata = 0
        while gid in self.gorusmeler and time.time() - bas < IZLEME_SINIRI:
            await self.uyu(IZLEME_ARALIK)
            gor = self.gorusmeler.get(gid)
            if gor is None:
                return
            try:
                durum, sure = await self.hat.durum(gor.sid)
                hata = 0
            except Exception as e:  # noqa: BLE001
                hata += 1
                logger.warning("arama durumu alınamadı: %s", type(e).__name__)
                if hata < 6:
                    continue
                durum, sure = "failed", 0
            if durum in BITTI:
                await self.bitti(gid, {"CallStatus": durum, "CallDuration": str(sure)})
                return
        self.gorusmeler.pop(gid, None)

    # --- ses ------------------------------------------------------------------------

    def _kalici(self, metin: str) -> str:
        """Sık kullanılan cümlelerin sesi bir kez üretilir, hep aynı URL (bekleme sesleri)."""
        sid = self.kalicilar.get(metin)
        g = self.sesler[sid][0] if sid in self.sesler else None
        if g is None or (g.done() and (g.cancelled() or g.exception() is not None)):
            sid = "k" + secrets.token_urlsafe(10)
            self.sesler[sid] = (_sessiz(asyncio.ensure_future(self._sentez_tekrarli(metin))), math.inf)
            self.kalicilar[metin] = sid
        return self._yol(f"ses/{sid}.mp3")

    def _ses_uret(self, metin: str) -> str:
        """Ses üretimini ARKA PLANDA başlatır, URL'yi hemen döner. Twilio'nun webhook'u 15 sn bekler; düşünme +
        ses üretimi bunu aşınca arama "application error" ile kapanıyordu. Twilio dosyayı isteyince ses_al bekler."""
        simdi = time.time()
        for k in [k for k, (_, t) in self.sesler.items() if simdi - t > SES_OMRU]:
            del self.sesler[k]
        sid = secrets.token_urlsafe(12)
        self.sesler[sid] = (_sessiz(asyncio.ensure_future(self._sentez_tekrarli(metin))), simdi)
        return self._yol(f"ses/{sid}.mp3")

    async def _sentez_tekrarli(self, metin: str) -> bytes:
        metin = tek_cumle(metin)
        if self._sentez_siniri is None:
            self._sentez_siniri = asyncio.Semaphore(AYNI_ANDA_SENTEZ)
        async with self._sentez_siniri:
            try:
                return await asyncio.wait_for(self.sentez(metin), SENTEZ_SURESI)
            except Exception as e:  # noqa: BLE001 — ağ kıpırtısı: bir kez daha
                logger.warning("telefon sesi ilk denemede üretilemedi: %s", type(e).__name__)
                return await asyncio.wait_for(self.sentez(metin), SENTEZ_SURESI)

    async def ses_al(self, sid: str) -> bytes | None:
        kayit = self.sesler.get(sid)
        if kayit is None:
            return None
        gorev = kayit[0]
        try:
            if gorev.done():  # hazırsa beklemeden (başka döngüden çağrılsa da güvenli)
                return gorev.result()
            return await asyncio.shield(gorev)
        except Exception as e:  # noqa: BLE001
            logger.warning("telefon sesi üretilemedi: %s", type(e).__name__)
            self.ses_hatalari = [*self.ses_hatalari[-4:], f"ses üretilemedi ({type(e).__name__})"]
            return None

    # --- Twilio olayları ------------------------------------------------------------

    async def basla(self, gid: str) -> str:
        gor = self.gorusmeler.get(gid)
        if gor is None or self.kilitli():
            return twiml_kapat(self._ses_uret("OpenDelegate şu an konuşamıyor, sohbetten yazacağım."))
        gor.basladi = True
        gor.iz.append(f"{self._sn(gor)}s açtın")
        sistem = TELEFON_SISTEMI + f"\nŞu an: {self.simdi():%Y-%m-%d %H:%M} (Türkiye).\nBu aramanın sebebi: {gor.neden}" + config.DIL_TALIMATI
        try:  # Twilio ~5 sn bekler: hafıza (telefon çalarken okunmaya başladı) hazır değilse hafızasız konuş
            gorev = gor.sistem_gorevi or asyncio.ensure_future(self.sistem())
            ek = await asyncio.wait_for(asyncio.shield(gorev), 1.0)
        except Exception as e:  # noqa: BLE001 — hafızasız da konuşur
            logger.warning("telefon hafıza bloğu alınamadı: %s", type(e).__name__)
            ek = ""
        gor.mesajlar = [{"role": "system", "content": sistem + ("\n\n" + ek if ek else "")},
                        {"role": "assistant", "content": gor.acilis}]
        gor.kayit.append(("Ben", gor.acilis))
        acilis = (await self._hazir(gor.acilis_sesi or self._ses_uret(gor.acilis), 1.5)  # çalarken üretildi
                  or await self._hazir(self._kalici("Efendim, OpenDelegate ben, dinliyorum."), 0.3))
        return twiml_dinle(acilis, self._yol(f"cevap?g={gid}"))

    async def cevap(self, gid: str, duyulan: str) -> str:
        gor = self.gorusmeler.get(gid)
        if gor is None:
            return twiml_kapat(self._ses_uret("Bağlantım koptu efendim, sohbetten devam edelim."))
        duyulan = (duyulan or "").strip()
        if not duyulan:
            gor.bos += 1
            if gor.bos >= 2:
                return await self._soyle_kapat(gor, "Seni duyamıyorum efendim, kapatıyorum. sohbetten yazarım.")
            return await self._soyle_dinle(gor, "Seni duyamadım, tekrar söyler misin?")
        gor.bos = 0
        gor.tur += 1
        gor.kayit.append(("Sen", duyulan))
        gor.iz.append(f"{self._sn(gor)}s sen konuştun")
        if not gor.konustu:
            gor.konustu = True
            if self.konustu_cb:
                try:
                    await self.konustu_cb(gor)
                except Exception as e:  # noqa: BLE001
                    logger.warning("konuştu bildirimi hatası: %s", type(e).__name__)
        onceki, gor.onceki = gor.onceki, None
        gor.bekleyen = asyncio.ensure_future(self._cevap_hazirla(gor, duyulan, onceki))
        gor.bekleme_bas, gor.dolgu_no = None, 0
        return await self._bekle_ya_da_dolgu(gor, ILK_BEKLEME)

    async def bekle(self, gid: str) -> str:
        """Twilio 'bakıyorum' sesinden sonra buraya döner: cevap hazırsa söyle, değilse bir dolgu daha."""
        gor = self.gorusmeler.get(gid)
        if gor is None:
            return twiml_kapat(self._kalici("Bağlantım koptu efendim, sohbetten devam edelim."))
        gor.iz.append(f"{self._sn(gor)}s bekle isteği")
        if gor.bekleyen is None:
            return twiml_dinle(await self._hazir(self._kalici("Dinliyorum efendim.")), self._yol(f"cevap?g={gid}"))
        return await self._bekle_ya_da_dolgu(gor, BEKLEME_ADIMI)

    async def _bekle_ya_da_dolgu(self, gor: Gorusme, sn: float) -> str:
        gorev = gor.bekleyen
        assert gorev is not None
        bitenler, _ = await asyncio.wait({gorev}, timeout=sn)
        if bitenler:
            gor.bekleyen = gor.bekleme_bas = None
            gor.iz.append(f"{self._sn(gor)}s cevap hazır")
            return gorev.result()
        simdi = time.time()
        if gor.bekleme_bas is None:
            gor.bekleme_bas = simdi
        elif simdi - gor.bekleme_bas > UZUN_IS:  # iş arka planda sürsün, konuşma devam etsin
            gor.onceki, gor.bekleyen, gor.bekleme_bas = gorev, None, None
            gor.kayit.append(("Ben", DOLGU_UZUN))
            return twiml_dinle(await self._hazir(self._kalici(DOLGU_UZUN), DOLGU_HAZIR),
                               self._yol(f"cevap?g={gor.id}"))
        n = gor.dolgu_no
        gor.dolgu_no += 1
        gor.iz.append(f"{self._sn(gor)}s bekleme {gor.dolgu_no}")
        if n == 0:
            url = await self._dolgu(DOLGU_ILK)
        elif n % 4 == 0:  # arada bir "hâlâ bakıyorum"; diğer turlarda kısa sessizlik (her 3 sn'de konuşmasın)
            url = await self._dolgu(DOLGU_SONRA[(n // 4 - 1) % len(DOLGU_SONRA)])
        else:
            url = None
        return twiml_bekle(url, self._yol(f"bekle?g={gor.id}"))

    async def _cevap_hazirla(self, gor: Gorusme, duyulan: str, onceki: "asyncio.Task[str] | None") -> str:
        """Düşün (+ araç/iş), sesi üret, TwiML'i döndür. Arka planda çalışır; hata vermez."""
        if onceki is not None:
            try:
                await onceki  # bırakılmış uzun iş bitmeden yeni tur geçmişe karışmasın
            except Exception:  # noqa: BLE001
                pass
        n = len(gor.mesajlar)
        gor.mesajlar.append({"role": "user", "content": duyulan})
        try:
            metin, kapat = await asyncio.wait_for(self._dusun(gor), DUSUNME_SURESI)
        except Exception as e:  # noqa: BLE001 — zaman aşımı dahil
            logger.warning("telefon düşünme hatası: %s", type(e).__name__)
            gor.iz.append(f"{self._sn(gor)}s düşünme hatası ({type(e).__name__})")
            uzun = isinstance(e, asyncio.TimeoutError)
            metin = ("Efendim, bu iş çok uzun sürdü, sonucu sohbete yazarım." if uzun
                     else "Efendim, bir şey ters gitti, bir daha söyler misin?")
            kapat = False
            # Yarım kalan tur (cevapsız tool çağrısı) sonraki her cevapta 400 verdirir: temizle, düz tur bırak.
            del gor.mesajlar[n:]
            gor.mesajlar += [{"role": "user", "content": duyulan}, {"role": "assistant", "content": metin}]
        if gor.tur >= MAKS_TUR and not kapat:
            metin, kapat = metin + " Uzun oldu, gerisini sohbetten konuşalım.", True
        gor.kayit.append(("Ben", metin))
        url = await self._hazir(self._ses_uret(metin), 2 * SENTEZ_SURESI + 2)  # Twilio isteyince hazır olsun
        if url is None:  # üretilemedi: hazır olmayan dosya = kopma; yazıya düş
            gor.iz.append(f"{self._sn(gor)}s cevap sesi üretilemedi")
            await self._gonder(f"Telefonda söyleyemediğim cevap: {metin}")
            url = await self._hazir(self._kalici(SES_TAKILDI), DOLGU_HAZIR)
        gor.kapattik = gor.kapattik or kapat
        return twiml_kapat(url) if kapat else twiml_dinle(url, self._yol(f"cevap?g={gor.id}"))

    @staticmethod
    def _sid(url: str) -> str:
        return url.rsplit("/", 1)[-1].removesuffix(".mp3")

    async def _hazir(self, url: str, sn: float = HAZIR_BEKLEME) -> str | None:
        """URL'yi SADECE ses hazır ve sağlamsa verir (en fazla `sn` bekler), değilse None.
        Hazır olmayan sesi Play'e vermek kopmaya yol açıyordu: Twilio dosyayı ister, üretim takılmışsa bekler,
        sabrı tükenince "please check your URL" der (15.11 teknik iz: 2. bekleme sesi hiç teslim edilmedi)."""
        kayit = self.sesler.get(self._sid(url))
        if kayit is None:
            return None
        g = kayit[0]
        if not g.done():
            try:
                await asyncio.wait_for(asyncio.shield(g), sn)
            except Exception:  # noqa: BLE001
                pass
        return url if g.done() and not g.cancelled() and g.exception() is None else None

    async def _dolgu(self, tercih: str) -> str | None:
        """İstenen bekleme sesi hazır değilse hazır olan başka bir bekleme sesi; hiçbiri yoksa None (sessizlik)."""
        for i, m in enumerate(dict.fromkeys((tercih, DOLGU_ILK, *DOLGU_SONRA))):
            url = await self._hazir(self._kalici(m), DOLGU_HAZIR if i == 0 else 0)
            if url:
                return url
        return None

    def isindir(self) -> None:
        """Sık sesler (bekleme, duyamadım...) önceden üretilir; hazır olanlara dokunmaz, bozulanı yeniden üretir."""
        for m in (DOLGU_ILK, *DOLGU_SONRA, DOLGU_UZUN, "Dinliyorum efendim.", "Seni duyamadım, tekrar söyler misin?",
                  "Seni duyamıyorum efendim, kapatıyorum. sohbetten yazarım.", SES_TAKILDI,
                  "Efendim, OpenDelegate ben, dinliyorum."):
            self._kalici(m)

    async def _soyle_dinle(self, gor: Gorusme, metin: str) -> str:
        gor.kayit.append(("Ben", metin))
        gor.iz.append(f"{self._sn(gor)}s dinle")
        return twiml_dinle(await self._hazir(self._kalici(metin)), self._yol(f"cevap?g={gor.id}"))

    async def _soyle_kapat(self, gor: Gorusme, metin: str) -> str:
        gor.kayit.append(("Ben", metin))
        gor.kapattik = True
        return twiml_kapat(await self._hazir(self._kalici(metin)))

    @staticmethod
    def _sn(gor: Gorusme) -> int:
        return int(time.time() - gor.baslangic)

    async def _dusun(self, gor: Gorusme, ek_araclar: dict[str, Any] | None = None,
                     ek_semalar: tuple[dict[str, Any], ...] = ()) -> tuple[str, bool]:
        """Model turu (+ en fazla MAKS_ARAC_TURU araç turu). (söylenecek metin, kapat mı)."""
        ek_adlar = {s["function"]["name"] for s in ek_semalar}
        semalar = [*(s for s in self.semalar if s["function"]["name"] not in ek_adlar), *ek_semalar, KAPAT_SEMASI]
        araclar = {**self.araclar, **(ek_araclar or {})}
        kapat = False
        for _ in range(MAKS_ARAC_TURU + 1):
            mesaj = await self.dusun(gor.mesajlar, semalar)
            cagrilar = list(getattr(mesaj, "tool_calls", None) or [])
            icerik = (getattr(mesaj, "content", None) or "").strip()
            if not cagrilar:
                gor.mesajlar.append({"role": "assistant", "content": icerik})
                return icerik or "Tamam efendim.", kapat
            gor.mesajlar.append({"role": "assistant", "content": icerik or None, "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.function.name,
                                                              "arguments": c.function.arguments or "{}"}}
                for c in cagrilar]})
            for c in cagrilar:
                ad = c.function.name
                if ad == "telefonu_kapat":
                    kapat, sonuc = True, "Telefon vedalaşma cümlenden sonra kapanacak."
                elif ad in araclar:
                    try:
                        arg = json.loads(c.function.arguments or "{}")
                        ok, sonuc = await araclar[ad](arg if isinstance(arg, dict) else {})
                    except Exception as e:  # noqa: BLE001
                        ok, sonuc = False, f"Araç hatası: {type(e).__name__}"
                    if ok:
                        gor.yapilanlar.append(sonuc)
                else:
                    sonuc = "Bu araç telefonda yok; sohbetten yapılır. Kullanıcıya öyle söyle."
                gor.mesajlar.append({"role": "tool", "tool_call_id": c.id, "content": sonuc[:1500]})
            if kapat and icerik:
                return icerik, True
        return "Tamam efendim, hallettim.", kapat

    async def uygulama_cevabi(self, soru: str, gecmis: list[dict[str, Any]] | None = None,
                              eylemler: list[dict[str, Any]] | None = None, ekran_ajani: bool = False) -> str:
        """Android uygulamasindan (/dinle) gelen sesli soruya cevap. Twilio yok, TwiML yok.

        Telefon beyninin araçları + uygulamaya özel olanlar (webde_ara). eylemler listesi verilirse (uygulama
        telefonda iş yapabilen sürümse) telefonda_yap da açılır; model çağırdıkça eylemler listeye eklenir.
        """
        simdi = self.simdi()
        sistem = UYGULAMA_SISTEMI + f"\nŞu an: {simdi:%Y-%m-%d %H:%M}, {_GUNLER[simdi.weekday()]} (Türkiye)." + config.DIL_TALIMATI
        ek_araclar: dict[str, Any] = dict(self.uygulama_araclari)
        ek_semalar = list(self.uygulama_semalari)
        if eylemler is not None:
            sistem += ("\n- Alarm, zamanlayıcı, uygulama açma, fener, birini arama, müzik ve ses: telefonda_yap "
                       "(anında olur, isi_yap DEĞİL); sonra 'kurdum efendim' gibi tek cümle söyle. Kimi arayacağını "
                       "net duymadıysan aramadan önce sor. Telefonun şu anki halini bilemezsin: geçmişte yapılmış "
                       "görünse de ('fener zaten açık' gibi) istenince telefonda_yap'ı YİNE çağır.")
            if ekran_ajani:
                sistem += ("\n- 'Telefonu kapat', 'telefonu kapatır mısın', 'telefonu kilitle', 'kilitler misin', "
                           "'ekranı kilitle', 'kitle', 'kilitle ve kapat' — fiil çekimi/soru biçimi farklı olsa da "
                           "HEPSİ AYNI İŞ: hemen telefonda_yap(eylem=ekran_kilitle) çağır, sorma. Bu konuşmayı "
                           "bitirme/vedalaşma isteği DEĞİLDİR; 'hoşçakal', 'görüşürüz' gibi bir cevap YAZMA, "
                           "doğrudan eylemi çağır ve 'Kilitledim efendim' gibi kısa söyle. Yan tuşa basmak gibi "
                           "ekranı kapatıp kilitler, telefon kapanmaz.")
                sistem += ("\n- 'Bildirimlerime bak', 'bildirimim var mı': telefonda_yap(eylem=bildirim_oku); "
                           "telefon bildirimleri kendisi sesli okur. SADECE kullanıcı bildirimleri açıkça isterse; "
                           "başka hiçbir soruda (tarih, hava, sohbet) bildirim_oku ÇAĞIRMA, geçmişte çağırdıysan bile.")
                sistem += ("\n- WhatsApp mesajı: önce kime ne yazacağını tek cümleyle sor (ör. 'Ahmet'e \"geliyorum\" "
                           "yazayım mı efendim?'); kullanıcı 'evet' deyince telefonda_yap(eylem=mesaj).\n"
                           "- Telefonda yapılacak başka HER iş (alarm silmek, bir uygulamada bir şey bulmak, ayar "
                           "değiştirmek...): telefonda_yap(eylem=ekranda, gorev=...); OpenDelegate telefonu ekrandan kendisi "
                           "kullanır. 'Telefondan yapamam' DEME.")
            hedef = eylemler

            async def telefonda_yap(arg: dict[str, Any]) -> tuple[bool, str]:
                eylem, sonuc = telefon_eylemi(arg)
                if eylem is not None:
                    hedef.append(eylem)
                return eylem is not None, sonuc
            ek_araclar["telefonda_yap"] = telefonda_yap
            ek_semalar.append(telefon_eylem_semasi(ekran_ajani))
        # Sesli turlar peş peşe gelir; hafıza bloğu (DB) her turda çekilmesin: 30 sn önbellek (hata önbelleğe girmez).
        simdi_m = time.monotonic()
        if simdi_m - _UYG_HAFIZA[0] < 30.0 and _UYG_HAFIZA[1]:
            ek = _UYG_HAFIZA[1]
        else:
            try:
                ek = await asyncio.wait_for(self.sistem(), 8.0)
                _UYG_HAFIZA[:] = [simdi_m, ek]
            except Exception as e:  # noqa: BLE001 — hafizasiz da konusur
                logger.warning("uygulama hafiza blogu alinamadi: %s", type(e).__name__)
                ek = ""
        gor = Gorusme(id="uygulama", neden="", acilis="", otomatik=False)
        gor.mesajlar = [{"role": "system", "content": sistem + ("\n\n" + ek if ek else "")},
                        *(gecmis or []),
                        {"role": "user", "content": soru}]
        metin, _ = await self._dusun(gor, ek_araclar, tuple(ek_semalar))
        return metin

    async def bitti(self, gid: str, form: dict[str, str]) -> None:
        """Twilio durum bildirimi: arama bitti (açıldı ya da açılmadı)."""
        durum = form.get("CallStatus", "")
        if durum not in BITTI:
            return
        gor = self.gorusmeler.pop(gid, None)
        try:
            sure = int(form.get("CallDuration") or 0)
        except ValueError:
            sure = 0
        if sure:
            self.durum["harcanan_sn"] = int(self.durum.get("harcanan_sn") or 0) + math.ceil(sure / 60) * 60
            await self._kaydet()
        if gor is not None:
            if gor.konustu:
                metin = self._ozet(gor, sure)
                if not gor.kapattik:  # ben kapatmadım: koptu ya da kullanıcı kapattı; son istekleri yaz (teşhis)
                    son = [m for t, m in self.olaylar if t >= gor.baslangic][-8:]
                    metin += ("\nTeknik iz (ben kapatmadım): " + " | ".join(son + gor.iz[-3:] + self.ses_hatalari[-2:]))
                twilio = await self._twilio_notlari(gor)
                if twilio:  # konuştuk ama Twilio bir hata/uyarı bıraktı (ör. arama erken kapandı)
                    metin += "\nTeknik not: " + twilio
            else:
                neden = {"busy": "meşguldün", "failed": "arama gitmedi"}.get(durum, "açmadın")
                metin = f"Efendim, seni aradım, {neden}. Şunun için aramıştım: {gor.neden}"
                if durum == "completed":
                    metin = f"Efendim, seni aradım ama konuşamadık (açmadın ya da sesli mesaja düştü). Şunun için aramıştım: {gor.neden}"
                if durum in ("completed", "failed"):
                    metin += "\n" + await self._teshis(gor)
            await self._gonder(metin)
        await self.kota_kontrol()

    async def _teshis(self, gor: Gorusme) -> str:
        """Konuşamadık: neden? (Twilio OpenDelegate'e ulaştı mı, imza tuttu mu, Twilio'nun notu)."""
        if gor.basladi:
            parca = ["Teknik not: arama bana ulaştı ama konuşma başlamadı"]
        elif self.imza_hatasi:
            parca = ["Teknik not: Twilio bana ulaştı ama istek bu aramaya ait görünmedi"]
        else:
            parca = ["Teknik not: Twilio bana hiç ulaşmadı (tuşa basılmadıysa normal)"]
        twilio = await self._twilio_notlari(gor)
        if twilio:
            parca.append(twilio)
        self.imza_hatasi = 0
        return ". ".join(parca) + "."

    async def _twilio_notlari(self, gor: Gorusme) -> str:
        notlar = getattr(self.hat, "notlar", None)
        if not (notlar and gor.sid):
            return ""
        try:
            n = await notlar(gor.sid)
        except Exception as e:  # noqa: BLE001
            logger.warning("Twilio notları alınamadı: %s", type(e).__name__)
            return ""
        return ("Twilio: " + " | ".join(n)) if n else ""

    def _ozet(self, gor: Gorusme, sure: int) -> str:
        dk = max(1, math.ceil(sure / 60)) if sure else None
        satirlar = [f"Telefon görüşmemiz bitti{f' ({dk} dk)' if dk else ''}. Konuştuklarımız:"]
        for kim, m in gor.kayit[-12:]:
            satirlar.append(f"{kim}: {m}")
        if gor.yapilanlar:
            satirlar.append("Yaptıklarım:")
            satirlar += [f"- {y}" for y in gor.yapilanlar]
        satirlar.append("Yanlış duyduğum bir şey varsa buradan düzelt.")
        return "\n".join(satirlar)

    async def _gonder(self, metin: str) -> None:
        try:
            await self.gonder_metin(metin)
            if self.bitti_cb:
                await self.bitti_cb(metin)
        except Exception as e:  # noqa: BLE001
            logger.warning("arama sonucu gönderilemedi: %s", type(e).__name__)

    # --- kota -----------------------------------------------------------------------

    def kalan_dakika(self) -> int:
        return DENEME_DAKIKA - int(self.durum.get("harcanan_sn") or 0) // 60

    async def kota_kontrol(self) -> None:
        """Deneme kotası azaldıysa ya da süre bitiyorsa birer kez haber verir."""
        uyarilar = set(self.durum.get("uyarilar") or [])
        kalan_gun = (DENEME_BITIS - self.simdi().date()).days
        mesaj = None
        if self.kalan_dakika() < KOTA_UYARI_DK and "dakika" not in uyarilar:
            uyarilar.add("dakika")
            mesaj = (f"Efendim, Twilio'nun bedava dakikaları azaldı (yaklaşık {max(0, self.kalan_dakika())} dk kaldı). "
                     "Bitince telefonla arayamam; sohbetten yazışmaya devam ederiz.")
        elif 0 <= kalan_gun <= KOTA_UYARI_GUN and "gun" not in uyarilar:
            uyarilar.add("gun")
            mesaj = (f"Efendim, Twilio deneme süresi {kalan_gun} gün sonra bitiyor. Sonra telefonla arayamam; "
                     "sohbetten yazışmaya devam ederiz.")
        if mesaj:
            self.durum["uyarilar"] = sorted(uyarilar)
            await self._kaydet()
            await self._gonder(mesaj)

    # --- tool -------------------------------------------------------------------------

    async def ayarla(self, arg: dict[str, Any]) -> tuple[bool, str]:
        """telefonla_ara tool'u: ara | durum."""
        eylem = str(arg.get("eylem") or "ara").strip()
        if eylem == "durum":
            g = self._gunluk()
            return True, (f"Telefon araması açık. Bugün {g['toplam']} arama. Deneme hesabında yaklaşık "
                          f"{max(0, self.kalan_dakika())} dk kaldı, süre {DENEME_BITIS:%d.%m.%Y}'de bitiyor.")
        if eylem != "ara":
            return False, "eylem 'ara' ya da 'durum' olmalı."
        soyle = re.sub(r"\s+", " ", str(arg.get("soyle") or "")).strip()
        neden = re.sub(r"\s+", " ", str(arg.get("neden") or "")).strip() or soyle or "sen istedin"
        if not soyle:
            acilis = "Efendim, OpenDelegate ben. Aramamı istemiştin, dinliyorum."
        elif any(a in soyle.casefold() for a in ("opendelegate", "delegate", "vekil")):  # model zaten kendini tanıttıysa tekrar etme
            acilis = soyle
        else:
            acilis = "Efendim, OpenDelegate ben. " + soyle
        ok, mesaj = await self.ara(neden, acilis, otomatik=False)
        return ok, (mesaj + " Açınca konuşuruz; bitince özetini buraya yazarım.") if ok else mesaj


# --- HTTP -----------------------------------------------------------------------------

def rotalar(al: Callable[[], Arama | None]) -> list[Any]:
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Route

    def xml(govde: str) -> Response:
        return Response(govde, media_type="application/xml")

    async def dogrula(request: Request) -> tuple[Arama, dict[str, str]] | None:
        a = al()
        if a is None or not hmac.compare_digest(str(request.path_params.get("token", "")).encode(), a.token.encode()):
            return None
        gor = a.gorusmeler.get(request.query_params.get("g", ""))
        if gor is None or not gor.sid:
            return None
        # request.form() python-multipart ister; Twilio düz urlencoded gönderir, kendimiz çözeriz.
        form = dict(parse_qsl((await request.body()).decode("utf-8", "replace"), keep_blank_values=True))
        yol = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        gelen = request.headers.get("X-Twilio-Signature", "")
        # Yedek: Render'ın ön sunucusunun ilettiği gerçek adres (WEBHOOK_URL yazılışı farklıysa).
        proto = request.headers.get("x-forwarded-proto", "https").split(",")[0].strip()
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        adaylar = dict.fromkeys([a.taban_url + yol] + ([f"{proto}://{host}{yol}"] if host else []))
        if not hmac.compare_digest(str(form.get("CallSid", "")).encode(), gor.sid.encode()):
            logger.warning("Twilio isteği bu aramaya ait değil (CallSid tutmadı)")
            a.imza_hatasi += 1
            return None
        if not any(imza_dogru(u, form, gelen, a.auth_token) for u in adaylar):
            # Gizli bilgi yazılmaz: sadece biçim ipuçları (nedenini sonra bulmak için).
            logger.warning("Twilio imzası tutmadı (CallSid bağıyla kabul): imza_var=%s tür=%s alan=%d host=%s",
                           bool(gelen), request.headers.get("content-type", "")[:60], len(form), host[:60])
        return a, form

    def izli(ad: str, isle: Callable[[Request], Awaitable[Response]]) -> Callable[[Request], Awaitable[Response]]:
        """Her Twilio isteğini kaydet: ne, sonuç kodu, süre, ne döndük (görüşme koparsa özete yazılır)."""
        async def sarili(request: Request) -> Response:
            bas = time.time()
            try:
                r = await isle(request)
            except asyncio.CancelledError:  # Twilio beklemekten vazgeçip bağlantıyı kesti
                a = al()
                if a is not None:
                    a.olay(f"{ad} YARIDA KALDI {time.time() - bas:.1f}sn")
                raise
            except Exception as e:  # noqa: BLE001 — 500 yerine kayıt + kibar kapanış
                logger.exception("telefon ucu patladı: %s", ad)
                a = al()
                if a is not None:
                    a.olay(f"{ad} PATLADI {type(e).__name__}")
                return xml(twiml_kapat(None))
            a = al()
            if a is not None:
                govde = bytes(r.body or b"")
                ne = ("bekleme sesi" if b"<Redirect" in govde else "dinle" if b"<Gather" in govde
                      else "kapat" if b"<Hangup" in govde else f"{len(govde)} bayt")
                a.olay(f"{ad} {r.status_code} {time.time() - bas:.1f}sn {ne}")
            return r
        return sarili

    async def basla(request: Request) -> Response:
        d = await dogrula(request)
        if d is None:
            return Response(status_code=403)
        return xml(await d[0].basla(request.query_params.get("g", "")))

    async def cevap(request: Request) -> Response:
        d = await dogrula(request)
        if d is None:
            return Response(status_code=403)
        return xml(await d[0].cevap(request.query_params.get("g", ""), d[1].get("SpeechResult", "")))

    async def bekle(request: Request) -> Response:
        d = await dogrula(request)
        if d is None:
            return Response(status_code=403)
        return xml(await d[0].bekle(request.query_params.get("g", "")))

    async def durum(request: Request) -> Response:
        d = await dogrula(request)
        if d is None:
            return Response(status_code=403)
        await d[0].bitti(request.query_params.get("g", ""), d[1])
        return Response(status_code=204)

    async def ses(request: Request) -> Response:
        a = al()
        if a is None or not hmac.compare_digest(str(request.path_params.get("token", "")).encode(), a.token.encode()):
            return Response(status_code=404)
        try:  # hazır olmayan ses normalde hiç verilmez; yine de sonsuza dek bekletme
            veri = await asyncio.wait_for(a.ses_al(request.path_params.get("sid", "")), 8.0)
        except asyncio.TimeoutError:
            veri = None
        if veri is None:
            return Response(status_code=404)
        return Response(veri, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    return [
        Route("/arama/{token}/basla", izli("başla", basla), methods=["POST"]),
        Route("/arama/{token}/cevap", izli("cevap", cevap), methods=["POST"]),
        Route("/arama/{token}/bekle", izli("bekle", bekle), methods=["POST"]),
        Route("/arama/{token}/durum", durum, methods=["POST"]),
        Route("/arama/{token}/ses/{sid}.mp3", izli("ses", ses), methods=["GET", "HEAD"]),
    ]
