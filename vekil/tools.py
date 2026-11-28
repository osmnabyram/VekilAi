"""OpenDelegate tool tanımları (Render tarafı).

Tool'lar burada ÇALIŞMAZ. Bu modül sadece şunları içerir:
- modele tanıtılan JSON şemaları,
- her tool'un nereye yönlendirileceği (hedef: "pc" ajanı ya da "onedrive"),
- risk değerlendirmesi (onay gerekip gerekmediğine bu karar verir).

Onay kuralı (Kullanıcının kararı, 15.11.2026: kritik terminal komutları da onaysız; sadece geri dönüşü
olmayan disk/sistem komutları ve ödeme onay ister. Kullanıcı izliyor, "dur" diyebiliyor):
- 🟢 düşük  -> onaysız, kısa bilgi (okuma/listeleme, web araması/okuma, hafıza notu).
- 🟡 orta   -> onaysız ama "🟡 ..." bilgi satırı + log: yazma (üzerine yazma dahil), silme, normal
  terminal, indirme, OneDrive yükleme/klasör/silme, program/pencere, ekran görüntüsü, hassas yol.
- ⛔ yasak  -> geri dönüşsüz disk/sistem komutları (_GERI_DONUSSUZ: format, diskpart...) HİÇ çalışmaz.
- 🔴 yüksek -> Telegram ✅/❌ onayı: bilinmeyen tool (ödeme onayı bilgisayar_kullan içinde).
  Diğer kritik komutlar (_TEHLIKELI: silme, taskkill, kayıt defteri...) 🟡: onaysız, bilgi satırı + log.
  Ödeme onayı bilgisayar_kullan içinde ayrıca sorulur (bilgisayar.py, odeme=true).

Hassas yollar (.env, .ssh, tarayıcı profilleri, Credentials...) en az 🟡 sayılır: onaysız ama
her zaman bilgi satırı + log bırakır. Ajan da yetki bayrağı olmayan yazma/terminal/hassas işi
çalıştırmaz (ajan/ajan.py); 🟡 işler ajana OpenDelegate'in yetkisiyle (onayli=True) gider.
OneDrive'da recursive silme yine YOK (onedrive.py), bu onay değil sabit kuraldır.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import config

@dataclass(frozen=True)
class Tool:
    ad: str
    hedef: str  # "pc" (ajan) | "onedrive" (rclone) | "web" (Render'da) | "hafiza" | "izleme" | "ordu"
    risk: str  # varsayılan risk
    aciklama: str
    parametreler: dict[str, Any]


_YOL = {"type": "string", "description": r"Windows yolu, ör. C:\Users\kullanici\OneDrive\Masaüstü\not.txt"}
_OD_YOL = {
    "type": "string",
    "description": f"OneDrive yolu, '/' ile. İzinli kökler: {', '.join(config.ONEDRIVE_KLASORLER)}. "
    "Ör. 'Belgeler/notlar.txt'. Kısayol: 'klasorler/...' = 'Masaüstü/klasorler/...'. "
    "Boş bırakılırsa izinli kökler listelenir.",
}


def _telefon_parametreleri() -> dict[str, Any]:
    """telefonda_yap: sesli uygulamanın telefon eylem şemasının aynısı (tek kaynak: arama.TELEFON_EYLEM_SEMASI)."""
    import arama  # tools hafif kalsın diye burada; arama tools'u içe aktarmaz
    return json.loads(json.dumps(arama.TELEFON_EYLEM_SEMASI["function"]["parameters"]))


TOOLLAR: dict[str, Tool] = {
    t.ad: t
    for t in [
        # ---- PC ajanı (PC açıkken) ----
        Tool(
            ad="dosya_ara",
            hedef="pc",
            risk="düşük",
            aciklama=(
                "[PC] Alt klasörlere inerek dosya ARAR: ada göre (parça ya da '*.pdf', 'odev*.py' gibi jokerli desen; "
                "büyük/küçük harf fark etmez) ve/veya İÇİNDEKİ metne göre (metin/kod dosyalarında, eşleşen satırı da "
                "verir). Bir dosyanın yerini bulmak için dosya_listele'yi elle gezmek ya da terminalde 'dir /s' yerine "
                "BUNU kullan. Kökü olabildiğince dar ver (ör. Masaüstü, Belgeler, proje klasörü). Hassas ve gereksiz "
                "klasörlere (AppData, node_modules, .git) girmez; en fazla 50 sonuç."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "kok": {"type": "string", "description": r"Aramanın başlayacağı klasör, ör. C:\Users\kullanici\OneDrive\Masaüstü. "
                            "Boşsa kullanıcı klasörü (~)."},
                    "ad": {"type": "string", "description": "Dosya adı parçası ya da deseni, ör. 'rapor' veya '*.docx'."},
                    "icerik": {"type": "string", "description": "Dosyanın içinde geçen metin (opsiyonel)."},
                    "derinlik": {"type": "integer", "description": "En fazla kaç alt klasör seviyesi (varsayılan 12)."},
                },
            },
        ),
        Tool(
            ad="dosya_listele",
            hedef="pc",
            risk="düşük",
            aciklama="[PC] Bir klasördeki dosya ve klasörleri listeler (alt klasörlere inmez). Sadece okur.",
            parametreler={
                "type": "object",
                "properties": {
                    "yol": _YOL,
                    "desen": {"type": "string", "description": "Glob filtresi, ör. *.pdf (varsayılan: *)"},
                },
                "required": ["yol"],
            },
        ),
        Tool(
            ad="dosya_oku",
            hedef="pc",
            risk="düşük",
            aciklama="[PC] Bir metin dosyasının içeriğini okur. Sadece okur.",
            parametreler={
                "type": "object",
                "properties": {
                    "yol": _YOL,
                    "maks_karakter": {"type": "integer", "description": "En fazla kaç karakter dönsün (varsayılan 4000)."},
                },
                "required": ["yol"],
            },
        ),
        Tool(
            ad="dosya_yaz",
            hedef="pc",
            risk="orta",
            aciklama=(
                "[PC] Bir metin dosyası oluşturur, üzerine yazar ya da sonuna ekler (UTF-8). Gerekirse klasörleri açar. "
                "Yeni dosya ve 'ekle' onaysız çalışır; VAR OLAN dosyanın üzerine yazmak kullanıcının onayını ister."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "yol": _YOL,
                    "icerik": {"type": "string", "description": "Yazılacak metin."},
                    "mod": {
                        "type": "string",
                        "enum": ["uzerine_yaz", "ekle"],
                        "description": "uzerine_yaz (varsayılan) ya da ekle.",
                    },
                },
                "required": ["yol", "icerik"],
            },
        ),
        Tool(
            ad="dosya_sil",
            hedef="pc",
            risk="orta",  # Kullanıcının kararı: silme onaysız (bilgi satırı + log); sadece kritik komut ve ödeme onaylı
            aciklama="[PC] Bir dosyayı ya da BOŞ bir klasörü siler. Geri dönüşüm kutusuna gitmez, kalıcıdır.",
            parametreler={
                "type": "object",
                "properties": {"yol": _YOL},
                "required": ["yol"],
            },
        ),
        Tool(
            ad="terminal_calistir",
            hedef="pc",
            risk="orta",
            aciklama=(
                "[PC] Windows cmd.exe'de bir komut çalıştırır ve çıktısını döner. "
                "PowerShell için: powershell -NoProfile -Command \"...\". "
                "Komutlar 60 sn sonra kesilir; etkileşimli komut kullanma."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "komut": {"type": "string", "description": "Çalıştırılacak komut."},
                    "calisma_dizini": {"type": "string", "description": "Komutun çalışacağı klasör (opsiyonel)."},
                },
                "required": ["komut"],
            },
        ),
        # ---- OneDrive (PC kapalıyken de çalışır) ----
        # Bilerek YOK: üzerine yazma, recursive (içeriğiyle) silme. Taşıma/yeniden adlandırma: onedrive_tasi.
        Tool(
            ad="onedrive_listele",
            hedef="onedrive",
            risk="düşük",
            aciklama="[OneDrive] Senkron bir OneDrive klasörünü bulut üzerinden listeler. PC kapalıyken de çalışır.",
            parametreler={"type": "object", "properties": {"yol": _OD_YOL}, "required": []},
        ),
        Tool(
            ad="onedrive_oku",
            hedef="onedrive",
            risk="düşük",
            aciklama="[OneDrive] OneDrive'daki bir metin dosyasını bulut üzerinden okur. PC kapalıyken de çalışır.",
            parametreler={"type": "object", "properties": {"yol": _OD_YOL}, "required": ["yol"]},
        ),
        Tool(
            ad="onedrive_ara",
            hedef="onedrive",
            risk="düşük",
            aciklama=(
                "[OneDrive] Adında verilen parça geçen dosya/klasörleri alt klasörler dahil arar (büyük/küçük harf "
                "duyarsız). Yerini bilmediğin dosya için önce bunu kullan. PC kapalıyken de çalışır."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "sorgu": {"type": "string", "description": "Ad parçası, ör. 'cv', 'rapor', '.pdf'."},
                    "kok": {
                        "type": "string",
                        "description": "Opsiyonel arama kökü, ör. 'klasorler' ya da 'Belgeler'. Boşsa tüm izinli kökler.",
                    },
                },
                "required": ["sorgu"],
            },
        ),
        Tool(
            ad="onedrive_gonder",
            hedef="onedrive",
            risk="düşük",  # salt okuma: dosya sadece kullanıcının kendi sohbetine gider
            aciklama=(
                "[OneDrive] OneDrive'daki bir dosyayı kullanıcının sohbetine gönderir, en fazla 20 MB. Resimler "
                "FOTOĞRAF, videolar VİDEO, GIF animasyon, ses dosyaları SES olarak doğrudan sohbette görünür; diğerleri "
                "(pdf, zip...) belge. 'Şu dosyayı at/gönder' denince kullan. PC kapalıyken de çalışır."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "yol": _OD_YOL,
                    "bicim": {
                        "type": "string",
                        "enum": ["otomatik", "belge"],
                        "description": "otomatik (varsayılan: resim/video medya olarak). Sadece kullanıcı açıkça 'dosya "
                        "olarak / sıkıştırmadan at' derse 'belge'.",
                    },
                },
                "required": ["yol"],
            },
        ),
        Tool(
            ad="onedrive_tasi",
            hedef="onedrive",
            risk="orta",  # onaysız (bilgi satırı + log); üzerine yazmaz
            aciklama=(
                "[OneDrive] Bir dosya ya da klasörü TAŞIR veya YENİDEN ADLANDIRIR (aynı klasörde yeni ad = yeniden "
                "adlandırma). Hedef '/' ile biterse o klasörün içine aynı adla taşır. Hedefte aynı adda bir şey varsa "
                "ÜZERİNE YAZMAZ, hata verir (başka ad seç). Sadece izinli kökler içinde; izinli kökün kendisi taşınamaz."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "kaynak": _OD_YOL,
                    "hedef": {"type": "string", "description": "Yeni yol, ör. 'Belgeler/arsiv/rapor-2026.pdf' ya da "
                              "klasörün içine taşımak için 'Belgeler/arsiv/'."},
                },
                "required": ["kaynak", "hedef"],
            },
        ),
        Tool(
            ad="onedrive_sil",
            hedef="onedrive",
            risk="orta",  # onaysız (bilgi satırı + log); recursive silme zaten yok
            aciklama=(
                "[OneDrive] Tek bir DOSYAYI ya da BOŞ bir klasörü siler (geri alınamaz). "
                "Recursive silme YOK: içinde dosya olan klasörü silemez. İzinli kökün kendisi silinemez."
            ),
            parametreler={"type": "object", "properties": {"yol": _OD_YOL}, "required": ["yol"]},
        ),
        Tool(
            ad="onedrive_yukle",
            hedef="onedrive",
            risk="orta",
            aciklama=(
                "[OneDrive] Bir URL'den dosya indirip ya da verilen metni OneDrive'a YENİ dosya olarak koyar "
                "(ör. masaüstünün bulut kopyası 'Masaüstü/'). PC kapalıyken de çalışır; PC açılınca senkronla "
                "PC'ye de iner. 'url' ya da 'icerik' parametrelerinden SADECE BİRİNİ ver. Var olan dosyanın "
                "üzerine asla yazmaz. Limit 25 MB."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "yol": {
                        "type": "string",
                        "description": "Hedef dosya yolu, ör. 'Masaüstü/rapor.txt'. URL ile yüklerken 'Masaüstü/' "
                        "gibi '/' ile biten bir klasör verilirse dosya adı URL'den alınır.",
                    },
                    "url": {"type": "string", "description": "İndirilecek http/https adresi."},
                    "icerik": {"type": "string", "description": "Dosyaya yazılacak metin (UTF-8)."},
                    "cakisma": {
                        "type": "string",
                        "enum": ["hata", "yeni_ad"],
                        "description": "Aynı adda dosya varsa: 'hata' (varsayılan) ya da 'yeni_ad' ('rapor 1.txt' gibi).",
                    },
                },
                "required": ["yol"],
            },
        ),
        Tool(
            ad="ekran_goruntusu",
            hedef="pc",
            risk="orta",  # onaysız (bilgi satırı + log); görüntü kullanıcının sohbetine de düşer
            aciklama=(
                "[PC] PC ekranının görüntüsünü alıp görsel olarak inceler ve ekranda ne olduğunu metin olarak döner. "
                "Kullanıcı 'ekrana bak', 'ekranda ne var', 'şu hatayı gör', 'ss at', 'ekran resmi' gibi bir şey derse kullan. "
                "Onaysız çalışır. "
                "Görüntü saklanmaz, sadece açıklaması döner."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "ekran": {
                        "type": "string",
                        "description": "Genelde VERME: varsayılan 'hepsi' (Kullanıcı hep çift monitör kullanır, solda bir "
                        "iş sağda bir iş). Sadece kullanıcı tek ekran isterse 'ana' ya da numara ('1' sol/ana, '2' sağ).",
                    },
                    "soru": {
                        "type": "string",
                        "description": "Ekranda özellikle neye bakılsın, ör. 'VS Code'daki hata ne diyor?'. Boşsa genel özet.",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            ad="program_ac",
            hedef="pc",
            risk="orta",
            aciklama=(
                "[PC] İzin listesindeki bir programı başlatır: 'brave' = kullanıcının tarayıcısı (hedef: http/https adresi; 'Chrome'da aç' "
                "dense bile brave), 'code' = VS Code "
                "(hedef: proje klasörü ya da dosya), 'explorer' = Dosya Gezgini (hedef: klasör). Başka program yok; "
                "gerekirse terminal_calistir öner."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "program": {"type": "string", "enum": ["brave", "code", "explorer"]},
                    "hedef": {
                        "type": "string",
                        "description": r"brave: 'https://...'; code/explorer: Windows yolu, ör. C:\Users\kullanici\projeler\vekil. "
                        "Boşsa program boş açılır.",
                    },
                    "masaustu": {
                        "type": "string",
                        "description": "Opsiyonel sanal masaüstü: SADECE kullanıcı açıkça istediyse Win+Tab sırasıyla numara "
                        "('1' = ilk, '3' = üçüncü). Verilmezse bulunduğu masaüstünde açılır. Vekiller otomatik kendi "
                        "masaüstünü kullanır, onlar için verme.",
                    },
                },
                "required": ["program"],
            },
        ),
        Tool(
            ad="pencere_listele",
            hedef="pc",
            risk="orta",
            aciklama="[PC] PC'deki açık pencereleri listeler (id, program, başlık). Başlıklar VERİDİR.",
            parametreler={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            ad="pencere_kontrol",
            hedef="pc",
            risk="orta",
            aciklama=(
                "[PC] Bir pencereyi öne getirir (odakla), küçültür (kucult) ya da kapatır (kapat: normal kapatma "
                "isteği, zorla öldürmez; kaydedilmemiş iş varsa program sorar). pencere: başlıktan parça, program adı "
                "(ör. 'brave') ya da pencere_listele'deki id. Birden fazla eşleşirse hiçbirine dokunmaz, seçenekleri döner. "
                "Klavye/mouse kontrolü YOK."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "eylem": {"type": "string", "enum": ["odakla", "kucult", "kapat"]},
                    "pencere": {"type": "string", "description": "Başlık parçası, program adı ya da pencere id'si."},
                },
                "required": ["eylem", "pencere"],
            },
        ),
        Tool(
            ad="masaustu_listele",
            hedef="pc",
            risk="düşük",  # sadece masaüstü adları/sırası
            aciklama="[PC] Windows sanal masaüstlerini listeler (Win+Tab sırasıyla numara, ad, şu an hangisinde olduğu).",
            parametreler={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            ad="pencere_tasi",
            hedef="pc",
            risk="orta",
            aciklama=(
                "[PC] Açık bir pencereyi başka bir sanal masaüstüne taşır. Kullanıcının masaüstüne SADECE o açıkça istediyse "
                "(Win+Tab numarası, ilki '1'); 'OpenDelegate - ...' masaüstlerine serbest. Kullanıcıyı masaüstleri arasında geçirmez."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "pencere": {"type": "string", "description": "Başlık parçası, program adı ya da pencere_listele id'si."},
                    "masaustu": {"type": "string", "description": "Hedef: numara ('2') ya da 'OpenDelegate - <ad>'."},
                },
                "required": ["pencere", "masaustu"],
            },
        ),
        Tool(
            ad="bilgisayari_uyut",
            hedef="pc",
            risk="orta",
            aciklama=(
                "[PC] Bilgisayarı hazırda bekletme (hibernate) moduna alır — kapatmaz, oturumu diske yazıp "
                "elektriği keser; akıllı priz + Wake-on-AC ile sonra anında kaldığı yerden açılır. Kullanıcı "
                "'işin bitince bilgisayarı kapat' derse: ASIL İŞİ BİTİR, sonra bunu çağır (sırayla, ayrı "
                "tool çağrısı). Düz 'bilgisayarı kapat/uyut' derse de bunu kullan, hiçbir zaman gerçek "
                "shutdown/kapatma yapma."
            ),
            parametreler={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            ad="izleme_yonet",
            hedef="izleme",
            risk="düşük",  # sadece okur/bildirir; hassas yollar reddedilir
            aciklama=(
                "[İzleme] PC'deki bir klasörü yeni dosyalar için izlemeye alır (ekle), izlenenleri listeler (liste) "
                "ya da bir izlemeyi kaldırır (kaldir). PC açıkken ajan dakikada bir bakar, yeni dosya düşünce sohbete "
                "haber gelir. Kurallar kalıcıdır."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "eylem": {"type": "string", "enum": ["ekle", "liste", "kaldir"]},
                    "yol": {"type": "string", "description": r"ekle: tam Windows klasör yolu, ör. C:\Users\kullanici\Downloads"},
                    "desen": {"type": "string", "description": "ekle: opsiyonel ad filtresi, ör. '*.pdf'. Boşsa hepsi."},
                    "derinlik": {"type": "integer", "description": "ekle: kaç alt klasör seviyesi (0-3, varsayılan 2)."},
                    "id": {"type": "integer", "description": "kaldir: kural numarası."},
                },
                "required": ["eylem"],
            },
        ),
        Tool(
            ad="brifing_ayarla",
            hedef="brifing",
            risk="düşük",  # sadece kendi ayarı
            aciklama=(
                "[Brifing] Her sabahki brifing mesajının ayarları. Kullanıcı 'brifinge X ekle', 'X'i çıkar', 'artık hava "
                "durumu verme', 'siber haberleri de koy', 'brifingi 9'a al', 'şehri değiştir', 'brifingde ne var', "
                "'brifingi şimdi bir dene' gibi bir şey dediğinde kullan. Bölümler: plan (bugünkü spor planı ve "
                "uyarılar), yapilacak (not edilen işler), hava, siber (en fazla 3 önemli siber haber), durum (OpenDelegate "
                "durumu), yorum (günün notu: hava ve plana göre kısa öneriler). Olmayan bir bölüm "
                "isterse (döviz, gündem vb.) henüz olmadığını dürüstçe söyle, uydurma."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "eylem": {"type": "string", "enum": ["goster", "ekle", "cikar", "saat", "sehir", "test"]},
                    "kategori": {"type": "string",
                                 "description": "ekle/cikar: yorum (günün notu) | plan | yapilacak | hava | "
                                                "siber | durum"},
                    "saat": {"type": "string", "description": "saat: 'SS:DD', ör. '09:00'"},
                    "sehir": {"type": "string", "description": "sehir: 'İlçe, İl' biçiminde. SADECE kullanıcı açıkça başka bir yer isterse değiştir; varsayılan Ümraniye, İstanbul (Kullanıcının evi)"},
                },
                "required": ["eylem"],
            },
        ),
        # ---- İnternet (Render'da çalışır, PC gerekmez) ----
        Tool(
            ad="webde_ara",
            hedef="web",
            risk="düşük",
            aciklama="[Web] İnternette arama yapar; başlık + link + kısa özet listesi döner. 'Nedir', 'nasıl yapılır', "
            "güncel bilgi, haber, fiyat, sürüm, karşılaştırma ya da emin olmadığın her konuda kendiliğinden önce bunu "
            "kullan, sonra gerekirse webde_oku ile en iyi linklere gir.",
            parametreler={
                "type": "object",
                "properties": {"sorgu": {"type": "string", "description": "Arama sorgusu (Türkçe ya da İngilizce)."}},
                "required": ["sorgu"],
            },
        ),
        Tool(
            ad="webde_oku",
            hedef="web",
            risk="düşük",
            aciklama="[Web] Bir web sayfasını açar, ana metnini çıkarır ve Türkçe özetini döner (5 MB limit). "
            "PDF/dosya için değil; onlar için indir kullan.",
            parametreler={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "http/https adresi."}},
                "required": ["url"],
            },
        ),
        Tool(
            ad="indir",
            hedef="web",
            risk="orta",
            aciklama=(
                "[Web] Bir URL'deki dosyayı indirir (25 MB limit). nereye='pc': PC ajanı çevrimiçiyse PC'ye "
                f"(varsayılan {config.PC_INDIRME_KLASORU}); ajan kapalıysa otomatik OneDrive Masaüstü'ne koyar. "
                "nereye='onedrive': OneDrive'a koyar (varsayılan 'Masaüstü/'). Var olan dosyanın üzerine yazmaz."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "İndirilecek http/https adresi."},
                    "nereye": {"type": "string", "enum": ["pc", "onedrive"], "description": "Varsayılan 'pc'."},
                    "hedef": {
                        "type": "string",
                        "description": "Opsiyonel klasör. pc için Windows yolu (ör. C:\\Users\\kullanici\\Desktop), "
                        "onedrive için 'Belgeler/' gibi.",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            ad="onedrive_klasor_olustur",
            hedef="onedrive",
            risk="orta",
            aciklama="[OneDrive] İzinli bir kökün altında klasör oluşturur (ara klasörlerle birlikte). Varsa dokunmaz.",
            parametreler={
                "type": "object",
                "properties": {"yol": {"type": "string", "description": "Ör. 'Belgeler/Projeler/OpenDelegate'."}},
                "required": ["yol"],
            },
        ),
        # ---- OpenDelegate ordusu (sadece Başvekil; işçi vekiller bunu göremez) ----
        Tool(
            ad="gorev_dagit",
            hedef="ordu",
            risk="düşük",  # kendisi bir şey yapmaz; işçilerin riskli eylemleri yine tek tek onaya gider
            aciklama=(
                "[Ordu] Çok parçalı bir işi işçi vekillere dağıtır; vekiller PARALEL çalışır, raporları sana döner, "
                "sen kullanıcıya tek rapor yazarsın. Sadece birbirinden bağımsız 2+ parçalı, paralelden gerçekten "
                "kazançlı işlerde kullan; basit işte, sohbette, tek aramada KULLANMA (her vekil ayrı token harcar). "
                "Roller: arastirmaci (web), kodcu (PC dosya/terminal), planlayici (salt okuma + plan). Aynı rolden "
                "birden fazla olabilir. 2+ vekilde sistem kullanıcıya kadroyu sorar."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "gorevler": {
                        "type": "array",
                        "description": f"En fazla {config.MAKS_VEKIL} görev. Vekiller konuşmayı GÖRMEZ: her tarif tek "
                        "başına anlaşılır olsun (yollar, linkler, kısıtlar içinde).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "vekil": {"type": "string", "enum": ["arastirmaci", "kodcu", "planlayici"]},
                                "gorev": {"type": "string", "description": "Ayrıntılı, kendi başına anlaşılır görev tarifi."},
                                "ozet": {"type": "string", "description": "Kullanıcıya gösterilecek 2-6 kelimelik özet."},
                            },
                            "required": ["vekil", "gorev", "ozet"],
                        },
                    },
                    "kadro_belirtildi": {
                        "type": "boolean",
                        "description": "Kullanıcı takımı (kaç vekil, kimler) bu konuşmada zaten açıkça söylediyse true; "
                        "o zaman tekrar sorulmaz. Emin değilsen verme.",
                    },
                },
                "required": ["gorevler"],
            },
        ),
        Tool(
            ad="bilgisayar_kullan",
            hedef="bilgisayar",
            risk="düşük",  # tam otonom; sadece para çıkışı adımı ayrıca onaya gider (bilgisayar.py)
            aciklama=(
                "[PC] Bilgisayarı fare ve klavyeyle kullanır: ekranı görür, tıklar, yazar — insan gibi. Dosya/terminal/"
                "program_ac YETMEDİĞİNDE, bir arayüzde gezinmek, bir uygulamada/sitede iş yapmak ya da 'şu ekranda ne "
                "var, özetle' gibi gözlem için kullan. Görevi tek, net bir cümleyle ver (hangi uygulama, ne yapılacak; "
                "gerekli metin/şifre görevde olsun). Onay sormadan başlar ve bitirir (Kullanıcı adımları izler); tek "
                "istisna para çıkışı (satın al/öde/havale) adımıdır, onu sorar."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "gorev": {"type": "string", "description": "Tek cümlelik net görev, ör. "
                              "'Hesap makinesinde 2+2 yap' ya da 'Not defterinde şu metni yaz: ...'."},
                    "ekran": {"type": "string", "description": "Genelde VERME: varsayılan 'otomatik' öndeki pencere hangi "
                              "monitördeyse onu izler. Sadece kullanıcı belirli bir monitör isterse numara ('1','2','3') ver."},
                },
                "required": ["gorev"],
            },
        ),
        Tool(
            ad="zihin_haritasi",
            hedef="harita",
            risk="düşük",  # sadece kullanıcıya kendi haritasının butonlarını gönderir
            aciklama=(
                "Kullanıcıya zihin haritanın (kalıcı hafızanın 3B evreni) açılış butonlarını gönderir: tarayıcıda "
                "açılan bağlantı. Kullanıcı 'zihnini aç', 'zihnini göster', 'beynini aç', 'hafızanı göster', "
                "'haritanı aç', 'aklındakileri göster' gibi bir şey derse HEMEN bunu çağır; ne istediğini sorma."
            ),
            parametreler={"type": "object", "properties": {}, "required": []},
        ),
        # ---- Kalıcı hafıza (deploy'larda silinmez) ----
        Tool(
            ad="hafizaya_yaz",
            hedef="hafiza",
            risk="düşük",
            aciklama=(
                "[Hafıza] kullanıcı hakkında öğrendiğin kalıcı bir bilgiyi kaydeder ya da günceller (aynı tur+anahtar "
                "varsa üzerine yazar). Kullanıcı bir terim/slang öğrettiğinde, bir tercih belirttiğinde ya da önemli "
                "bir karar verdiğinde HEMEN kullan. GÜNCELLEMEK için aynı tur+anahtarla yeni değeri yaz (eskiyi ezer); "
                "bilgi değiştiyse (ör. yeni kuşak, yeni iş, taşınma) eskisini güncelle, çift kayıt açma. "
                "Şifre, key, token gibi sırları ASLA yazma."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "tur": {
                        "type": "string",
                        "enum": ["tercih", "terim", "karar", "not"],
                        "description": "tercih: nasıl davranmanı istediği; terim: slang/kısaltma anlamı; "
                        "karar: proje/iş kararı; not: diğer kalıcı bilgiler.",
                    },
                    "anahtar": {"type": "string", "description": "Kısa başlık, ör. 'pist', 'cevap uzunluğu'."},
                    "deger": {"type": "string", "description": "Bilginin kendisi, 1-2 cümle."},
                },
                "required": ["tur", "anahtar", "deger"],
            },
        ),
        Tool(
            ad="hafizadan_sil",
            hedef="hafiza_sil",
            risk="orta",  # onaysız ama "🟡" bilgi satırı: Kullanıcı neyin silindiğini görür
            aciklama=(
                "[Hafıza] Kalıcı hafızadan bir bilgiyi siler. Sadece bilgi YANLIŞ, ESKİMİŞ ya da kullanıcı 'bunu unut' "
                "dediğinde kullan; değişen bilgi için silme, hafizaya_yaz ile güncelle. Anahtar hafıza bloğundaki "
                "adıyla birebir olmalı (ör. 'profil_12'). Emin değilsen silme, kullanıcıya sor."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "anahtar": {"type": "string", "description": "Silinecek bilginin anahtarı."},
                    "neden": {"type": "string", "description": "Kısa sebep (Kullanıcıya gösterilir)."},
                },
                "required": ["anahtar", "neden"],
            },
        ),
        Tool(
            ad="yapilacak",
            hedef="yapilacak",
            risk="düşük",
            aciklama=(
                "[Yapılacaklar] SAATSİZ işler: Kullanıcı 'bugün/yarın şunu yapacağım', 'X'i unutmayayım' dediğinde ekle "
                "(tarih verdiyse tarih ile). İşler sabah brifinginde görünür. Kullanıcı 'yaptım', 'bitti', 'hallettim' "
                "deyince bitti ile sil. 'Ne işlerim var' derse listele. SAAT verilirse ('15:00'te dürt', 'akşam 8'de "
                "hatırlat') bunu değil zamanla tool'unu kullan."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "eylem": {"type": "string", "enum": ["ekle", "listele", "bitti"]},
                    "metin": {"type": "string", "description": "ekle: işin kısa tanımı; bitti: id yoksa işin bir parçası."},
                    "tarih": {"type": "string", "description": "ekle: 'YYYY-MM-DD', 'bugün' ya da 'yarın'. Tarihsizse boş."},
                    "id": {"type": "integer", "description": "bitti: iş numarası (listede #3 gibi)."},
                },
                "required": ["eylem"],
            },
        ),
        Tool(
            ad="zamanla",
            hedef="zamanla",
            risk="düşük",  # sadece zamanlar; saati gelince yapılan işler normal risk kurallarından geçer
            aciklama=(
                "[Zamanla] SAATLİ hatırlatma ya da zamanlanmış görev. 'Saat 15:00'te beni dürt', 'yarın 9:30'da toplantıyı "
                "hatırlat' -> tur=hatirlat (o saatte kullanıcıya mesaj). 'Bu akşam 22:00'de rapor klasörünü OneDrive'a "
                "yedekle', 'her gün 23:00'de indirilenler klasörünü temizle' -> tur=gorev (o saatte işi SEN kendin "
                "yaparsın; metne işi tek başına anlaşılır yaz: yol, ne yapılacak). 'Her gün/her hafta' -> tekrar. Zamanı "
                "[Anlık durum]'daki şu anki saate göre hesapla. Listelemek ya da iptal etmek için listele/sil."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "eylem": {"type": "string", "enum": ["ekle", "listele", "sil"]},
                    "zaman": {"type": "string", "description": "ekle: 'SS:DD' (bugün; geçtiyse yarın), 'yarın SS:DD' ya "
                              "da 'YYYY-AA-GG SS:DD' (Türkiye saati)."},
                    "metin": {"type": "string", "description": "ekle: hatırlatılacak şey ya da yapılacak işin tarifi."},
                    "tur": {"type": "string", "enum": ["hatirlat", "gorev"]},
                    "tekrar": {"type": "string", "enum": ["yok", "gunluk", "haftalik"]},
                    "id": {"type": "integer", "description": "sil: kayıt numarası (listede #3 gibi)."},
                    "acil": {"type": "boolean", "description": "Kullanıcı 'acil' dediyse true: saati gelince sesli not "
                             "zinciri başlar (şimdi, 10 dk, 20 dk; cevap verirse durur)."},
                    "ara": {"type": "boolean", "description": "tur=hatirlat: saati gelince kullanıcıyı TELEFONLA ara "
                            "('arayarak hatırlat' dediyse ya da kaçırmaması gereken önemli bir şeyse)."},
                },
                "required": ["eylem"],
            },
        ),
        Tool(
            ad="hava_durumu",
            hedef="hava",
            risk="düşük",
            aciklama=(
                "[Hava] HIZLI hava durumu: şu an, önümüzdeki saatler ve 3 günlük tahmin, tek adımda (~1 sn). Hava, "
                "sıcaklık, yağmur, 'ne giyeyim', 'şemsiye lazım mı' sorularında İLK bunu kullan; internette ARAMA. "
                "yer boşsa kullanıcının şehri (Ümraniye). Araç iki kaynağı da (Open-Meteo + MET Norway) deneyip hata "
                "verirse hava bilgisi verme, internette eski/tarihli sayfa okuma; kullanıcıya hava verisini şu an "
                "doğrulayamadığını söyle."
            ),
            parametreler={
                "type": "object",
                "properties": {"yer": {"type": "string", "description": "İlçe/şehir (ör. 'Kadıköy'). Boş = Ümraniye."}},
            },
        ),
        Tool(
            ad="telefonda_yap",
            hedef="telefon_is",
            risk="düşük",
            aciklama=(
                "[Telefon] kullanıcının ANDROID telefonunda iş yapar (OpenDelegate uygulaması açık ve internete "
                "bağlıysa): alarm, zamanlayıcı, uygulama açma, fener, rehberden arama, müzik/ses, ekranı kilitleme "
                "(ekran_kilitle; 'telefonu kapat' da budur), WhatsApp mesajı (mesaj), bildirimleri okuma (bildirim_oku, "
                "SADECE kullanıcı isterse) ve geri kalan her işi telefonun ekranından kendisi yapma (ekranda, gorev=tam "
                "cümle). Kullanıcı sohbetten 'telefonda şunu yap' dediğinde kullan; bilgisayar işi için DEĞİL. Telefon "
                "işi yapıp sonucu döner (ekrandan işler 1-2 dk sürebilir); sonucu kısaca söyle. Para/banka işi yapılmaz."
            ),
            parametreler=_telefon_parametreleri(),
        ),
        Tool(
            ad="telefonla_ara",
            hedef="telefon",
            risk="düşük",
            aciklama=(
                "[Telefon] kullanıcıyı telefonla ARAR, açınca sesli karşılıklı konuşursunuz. Kullanıcı 'beni ara' dediğinde YA DA "
                "önemli/acil bir şey olduğunda (bir iş patladı, kaçırmaması gereken bir durum) kendi kararınla kullan; "
                "her saatte arayabilirsin. Önemsiz şeyler için arama, sohbet yeter. eylem=ara: soyle = açılışta söyleyeceğin kısa cümle (ör. 'Toplantın 15 dakika sonra "
                "başlıyor.'), neden = kullanıcı açmazsa sohbete yazılacak kısa sebep. İLERİ bir saatte aramak için "
                "('8'de ara', '10 dk sonra ara') bunu değil zamanla(tur=gorev, metin='Kullanıcıyı telefonla ara: <ne için>') "
                "kullan. eylem=durum: kalan deneme dakikası. Görüşme bitince özet sohbete kendiliğinden gelir."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "eylem": {"type": "string", "enum": ["ara", "durum"]},
                    "soyle": {"type": "string", "description": "ara: Kullanıcı açınca ilk söylenecek kısa cümle."},
                    "neden": {"type": "string", "description": "ara: aramanın kısa sebebi."},
                },
                "required": ["eylem"],
            },
        ),
        Tool(
            ad="pc_guc",
            hedef="pc_guc",
            risk="düşük",
            aciklama=(
                "[PC gücü] Evdeki bilgisayarı uzaktan AÇAR (akıllı priz: elektrik gelince laptop hazırda bekletten "
                "uyanır, 1-2 dk sürer). Kullanıcı bilgisayarı açmanı isterse (nasıl söylediği fark etmez) eylem=ac. "
                "Bilgisayar gerektiren bir işte (ekran görüntüsü, dosya, terminal, bilgisayar_kullan) PC kapalıysa "
                "bunu ayrıca çağırma: OpenDelegate o işten önce kendisi açmayı dener. eylem=durum: PC açık mı, uzaktan açma "
                "kurulu mu. Açılmazsa kullanıcıya dürüstçe söyle; ödeme/yükseltme önerme."
            ),
            parametreler={"type": "object", "required": ["eylem"], "properties": {
                "eylem": {"type": "string", "enum": ["ac", "durum"]}}},
        ),
        Tool(
            ad="komut_calistir",
            hedef="komut_calistir",
            risk="düşük",
            aciklama=(
                "[Sohbet komutları] kullanıcının isteği bir bot komutuyla karşılanıyorsa (nasıl söylediği fark etmez) "
                "komutu SEN çalıştır, kullanıcının /komut yazmasını bekleme: durum "
                "(OpenDelegate/PC/OneDrive durumu), zihin (zihin haritası linki), hafiza (kayıtlı "
                "bilgiler), masaustu (masaüstleri), brifing (sabah brifingini şimdi gönder), grind (son "
                "geliştirmeler), kuyruk (PC kapalıyken sıradaki işler), gecmis (son eylemler), vekiller (işçi "
                "vekiller), izle (izlenen klasörler), hiz (son işlerin süreleri: 'neden yavaşsın'). Çıktı sohbete doğrudan gider."
            ),
            parametreler={"type": "object", "required": ["komut"], "properties": {"komut": {"type": "string", "enum": [
                "durum", "zihin", "hafiza", "masaustu", "brifing", "grind", "kuyruk", "gecmis",
                "vekiller", "izle", "hiz"]}}},
        ),
        Tool(
            ad="gelisim_gecmisi",
            hedef="gelisim_gecmisi",
            risk="düşük",
            aciklama=(
                "[OpenDelegate] Kendi geliştirme geçmişin (son commit'ler, tarih ve saatiyle; /grind ile aynı). Kullanıcı "
                "'gelişmeleri göster', 'neler eklendi', 'son güncellemeler', 'commit geçmişin' gibi ne sorarsa BUNU "
                "kullan; PC'de git çalıştırma. adet: kaç tane (varsayılan 20)."
            ),
            parametreler={"type": "object", "properties": {
                "adet": {"type": "integer", "description": "Kaç geliştirme (1-50, varsayılan 20)"}}},
        ),
        Tool(
            ad="oz_degerlendirme",
            hedef="oz_degerlendirme",
            risk="düşük",
            aciklama=(
                "[Öz-değerlendirme] Pazar gecesi haftalık raporunda kendi davranışın için öneriler (#numaralı) "
                "verirsin; bunlar kullanıcı onaylayana kadar UYGULANMAZ. Kullanıcı bir öneriyi kabul ederse (nasıl söylediği "
                "fark etmez) eylem=onayla, no; istemezse reddet; bekleyenleri görmek için listele. Bu haftaki raporu "
                "istemezse ('bu hafta rapor atma', 'sus') eylem=sus."
            ),
            parametreler={"type": "object", "required": ["eylem"], "properties": {
                "eylem": {"type": "string", "enum": ["listele", "onayla", "reddet", "sus"]},
                "no": {"type": "integer", "description": "onayla/reddet: öneri numarası"},
            }},
        ),
        Tool(
            ad="hafiza_kontrol",
            hedef="hafiza_kontrol",
            risk="düşük",
            aciklama=(
                "[Hafıza] kullanıcı hafızanı kontrol etmeni isterse (nasıl söylediği fark etmez): kalıcı kayıtlarda "
                "çakışan, eskimiş ve çelişen olanları raporlar. SALT OKUMA: hiçbir şeyi silmez; temizliği kullanıcı "
                "söylerse hafizadan_sil ile, önce hangilerini sileceğini ona sorup yap."
            ),
            parametreler={"type": "object", "properties": {}},
        ),
        Tool(
            ad="konu_takip",
            hedef="konu_takip",
            risk="düşük",
            aciklama=(
                "[Konu takibi] OpenDelegate açık kalan konuları haftada en fazla bir kez kendiliğinden sorar. Kullanıcı son "
                "sorduğun konuyu bir daha sormamanı isterse (nasıl söylediği fark etmez: 'boşver', 'kapattım o işi', "
                "'sorma artık'...) eylem=sustur. Hiçbir konuyu sormamanı isterse hepsini_sustur; tekrar sormana izin "
                "verirse ac."
            ),
            parametreler={
                "type": "object",
                "properties": {"eylem": {"type": "string", "enum": ["sustur", "hepsini_sustur", "ac"]}},
                "required": ["eylem"],
            },
        ),
        Tool(
            ad="ses",
            hedef="ses",
            risk="düşük",
            aciklama=(
                "[Sesli cevap] Türkçe sesli not. Kullanıcı cevabı duymak isterse (nasıl söylediği fark etmez: 'sesli "
                "anlat', 'bunu da sesli söyle', 'dinleyeyim', 'okur musun'...) eylem=cevabi_sesli: o turdaki SON "
                "cevabın yazıldıktan sonra sesli de gider (metni argümana koyma). eylem=oku: sadece belirli bir metni "
                "seslendirmek için (ör. bir dosyadaki yazıyı okut). "
                "OpenDelegate'in tek, sabit bir sesi var (değiştirilemez). eylem=ayarla: mod ('kapali' = sadece istenince, "
                "'sesliye_sesli' = sesli mesaja sesli cevap, 'hepsi' = her cevap sesli de), hiz ('+10%', '-10%'). "
                "Tercih kalıcı kaydedilir. eylem=acil: 'acil' dediği şey için ŞİMDİ sesli hatırlatma zinciri başlatır "
                "(şimdi, 10 dk, 20 dk; cevap verirse durur, cevapsızsa yapılacaklara sıraya girer). Saatli acil için "
                "zamanla(acil=true) kullan. eylem=acil_dur: zinciri durdurur. eylem=durum: mevcut ses ayarı."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "eylem": {"type": "string", "enum": ["cevabi_sesli", "oku", "ayarla", "durum", "acil", "acil_dur"]},
                    "metin": {"type": "string", "description": "oku/acil: seslendirilecek metin."},
                    "mod": {"type": "string", "enum": ["kapali", "sesliye_sesli", "hepsi"]},
                    "hiz": {"type": "string", "description": "ayarla: konuşma hızı, ör. '+10%'."},
                },
                "required": ["eylem"],
            },
        ),
        Tool(
            ad="is_defteri",
            hedef="is",
            risk="düşük",
            aciklama=(
                "Uzun işlerde kaldığı yeri kaybetmemek için: eylem='ekle' yeni iş açar, 'guncelle' adım/dosyayı "
                "günceller (id zorunlu), 'bitti' tamamlandı işaretler, 'listele' tüm işleri gösterir, 'sil' siler. "
                "Birden fazla adım sürecek işe başlarken ekle; her önemli adımda guncelle; bitince bitti de."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "eylem": {"type": "string", "enum": ["ekle", "guncelle", "bitti", "listele", "sil"]},
                    "id": {"type": "string", "description": "İş kimliği (guncelle/bitti/sil için zorunlu)."},
                    "baslik": {"type": "string", "description": "İşin kısa adı (ekle için zorunlu)."},
                    "dosya": {"type": "string", "description": "İlgili dosya yolu."},
                    "adim": {"type": "string", "description": "Şu anki adım / sıradaki yapılacak."},
                },
                "required": ["eylem"],
            },
        ),
        Tool(
            ad="kod_degisiklik",
            hedef="pc",
            risk="orta",
            aciklama=(
                "[PC] C:\\vekil repo'sundaki bir kaynak dosyada eski_metin → yeni_metin değişikliği yapar (ilk eşleşme). "
                "OpenDelegate kendi kodunu düzeltmek ya da geliştirmek istediğinde kullan: aciklama alanına ne değiştirdiğini "
                "ve neden yaz. Sadece C:\\vekil altındaki dosyalara dokunur."
            ),
            parametreler={
                "type": "object",
                "properties": {
                    "dosya_yolu": {"type": "string", "description": "Değiştirilecek dosya (ör. C:\\vekil\\main.py)."},
                    "eski_metin": {"type": "string", "description": "Dosyada değiştirilecek tam metin (eşsiz olmalı)."},
                    "yeni_metin": {"type": "string", "description": "Yerine gelecek metin."},
                    "aciklama": {"type": "string", "description": "Ne değiştirdi ve neden (Kullanıcı onay ekranında görür)."},
                },
                "required": ["dosya_yolu", "eski_metin", "yeni_metin", "aciklama"],
            },
        ),
    ]
}


# --------------------------------------------------------------------------
# Risk
# --------------------------------------------------------------------------

# Terminal komutunda geçerse riski "yüksek" yapan ifadeler (küçük harfle aranır).
# KARA LİSTE (Kullanıcının kararı 15.11): geri dönüşü olmayan disk/sistem komutları HİÇBİR KOŞULDA çalışmaz,
# Kullanıcı "yap" dese bile (risk "yasak" -> reddedilir, onay bile sorulmaz).
_GERI_DONUSSUZ = (" format ", "format-volume", "diskpart", "bcdedit", " cipher ", "clear-disk", "initialize-disk",
                  "remove-partition", "vssadmin delete", "wbadmin delete", "reagentc /disable", "manage-bde")

_TEHLIKELI = (
    " del ", " erase ", "rmdir", " rd ", " rm ", "remove-item", " format ", "diskpart",
    "shutdown", "restart-computer", "stop-computer", " reg ", "regedit", "bcdedit",
    "taskkill", "stop-process", "icacls", "takeown", " cipher ", " move ", " ren ",
    "set-executionpolicy", "invoke-webrequest", " iwr ", " curl ", " wget ", "net user",
    ">",
)

# Hassas yollar. Yollar küçük harfe çevrilip '/' -> '\' yapıldıktan sonra aranır.
# NOT: ajan/ajan.py içinde aynı liste var; değiştirirsen ikisini birlikte güncelle.
_HASSAS = [
    re.compile(p)
    for p in (
        r"(^|[\\\s\"'=])\.env(\.[\w.-]+)?($|[\\\s\"'])",  # .env, .env.local ...
        r"\\\.(ssh|aws|azure|gnupg|kube|docker)(\\|$)",
        r"\.git-credentials|\.netrc|\.npmrc|\.pypirc",
        r"(^|\\)id_(rsa|ed25519|ecdsa|dsa)",
        r"\.(pem|key|pfx|p12|kdbx|ppk)($|[\s\"'])",
        r"credentials",  # Microsoft\Credentials, .aws\credentials ...
        r"\\microsoft\\(protect|vault)(\\|$)",
        r"\\windows\\system32\\config(\\|$)",  # SAM / SYSTEM kovanları
        # Tarayıcı profilleri (şifreler, çerezler)
        r"\\appdata\\(local|roaming)\\(google\\chrome|microsoft\\edge|bravesoftware|vivaldi"
        r"|opera software|mozilla\\firefox|yandex)",
    )
]


def hassas_mi(metin: str) -> bool:
    """Metinde (yol ya da komut) hassas bir konum geçiyor mu?"""
    m = metin.lower().replace("/", "\\")
    return any(p.search(m) for p in _HASSAS)


def risk_degerlendir(ad: str, argumanlar: dict[str, Any]) -> str:
    """Bir tool çağrısının risk seviyesi: "düşük" | "orta" | "yüksek" | "bilinmiyor"."""
    tool = TOOLLAR.get(ad)
    if tool is None:
        return "bilinmiyor"
    risk = tool.risk

    if ad == "terminal_calistir":
        komut = f" {str(argumanlar.get('komut', '')).lower()} "
        if any(k in komut for k in _GERI_DONUSSUZ):
            return "yasak"  # diski/sistemi silen-bozan: "dur" geri alamaz; asla çalıştırılmaz
        elif any(k in komut for k in _TEHLIKELI):
            risk = "orta"  # Kullanıcının kararı (15.11): kritik komut onaysız; bilgi satırı + log, "dur" ile kesilir

    if ad == "is_defteri":
        return "düşük"
    if ad == "kod_degisiklik":
        return "orta"

    if ad in ("webde_ara", "webde_oku", "hafizaya_yaz", "izleme_yonet", "ses", "konu_takip", "hafiza_kontrol", "oz_degerlendirme", "gelisim_gecmisi", "komut_calistir", "gorev_dagit", "bilgisayar_kullan", "zihin_haritasi",
              "brifing_ayarla", "yapilacak", "zamanla", "telefonla_ara", "telefonda_yap", "hava_durumu", "pc_guc", "takvim_bak"):
        return risk  # internetten okur / kendi hafızasına yazar; PC/OneDrive yoluna dokunmaz
    yazan = ad in ("dosya_yaz", "onedrive_yukle", "indir")
    if yazan:
        # içeriği/URL'yi değil, hedef yolu kontrol et
        kontrol = str(argumanlar.get("yol") or argumanlar.get("hedef") or "")
    else:
        kontrol = " ".join(str(v) for v in argumanlar.values() if isinstance(v, str))
    if hassas_mi(kontrol) and risk == "düşük":
        risk = "orta"  # hassas yol: onaysız ama mutlaka bilgi satırı + log (ve ajana yetki bayrağı)
    return risk


def onay_gerekir(risk: str) -> bool:
    """Sadece 🔴 (= tehlikeli terminal komutu) ve bilinmeyen tool Telegram onayı ister; 🟡 bilgi satırıyla onaysız."""
    return risk not in ("düşük", "orta")


def yetkili(risk: str) -> bool:
    """Ajana/web'e giden 'onayli' bayrağı: 🟡 politika gereği yetkili, 🔴 sadece onaydan sonra buraya gelir."""
    return risk != "düşük"


# --------------------------------------------------------------------------
# Model için yardımcılar
# --------------------------------------------------------------------------

def openai_semalari() -> list[dict[str, Any]]:
    """Tool'ları OpenAI/DeepSeek `tools` parametresi formatında döner."""
    return [
        {
            "type": "function",
            "function": {"name": t.ad, "description": t.aciklama, "parameters": t.parametreler},
        }
        for t in TOOLLAR.values()
    ]


def argumanlari_coz(ham: str | None) -> dict[str, Any]:
    """Modelin döndüğü JSON argüman metnini sözlüğe çevirir."""
    if not ham:
        return {}
    try:
        veri = json.loads(ham)
    except json.JSONDecodeError as e:
        raise ValueError(f"Argümanlar geçerli JSON değil: {e}") from e
    if not isinstance(veri, dict):
        raise ValueError("Argümanlar bir JSON nesnesi olmalı.")
    return veri


def kisalt(metin: str, limit: int = config.CIKTI_LIMITI) -> str:
    """Uzun metni kısaltır ve kaç karakterin kesildiğini belirtir."""
    if len(metin) <= limit:
        return metin
    return metin[:limit] + f"\n... [{len(metin) - limit} karakter kesildi]"
