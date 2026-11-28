# Değişiklikler / Changelog

Biçim: [Keep a Changelog](https://keepachangelog.com/). Sürümler [Releases](https://github.com/osmnabyram/OpenDelegate/releases) sayfasında.

## [Yayınlanmadı / Unreleased]

### Değişti / Changed
- Web kullanıcısına görünen "Telegram" ifadeleri (hata mesajları, /yardim, durum, zihin haritası ipuçları, asistanın kendi araç açıklamaları) sohbet olarak değiştirildi. / User-facing "Telegram" wording (error messages, /help, status, mind-map hints, the assistant's own tool descriptions) now says chat.
- **Web sohbetinin giriş şifresi artık `kys` şifresiyle aynı.** Ayrı `WEB_PAROLA` yok: tek şifre, yalnızca PBKDF2 özeti saklanır (şifrenin kendisi hiçbir yerde durmaz). Kurulum sihirbazında şifre en az 12 karakter ve tek sayfada sorulur; Render şablonu `KILIT_*` özetlerini ister. `WEB_PAROLA` yalnızca kys şifresi hiç ayarlanmamış kurulumlar için yedek olarak kaldı. / **The web chat sign-in password is now the same as the `kys` password.** No separate `WEB_PAROLA`: one password, only its PBKDF2 digest is stored (the password itself is never kept). The setup wizard asks for it once, at least 12 characters; the Render blueprint asks for the `KILIT_*` digests. `WEB_PAROLA` remains only as a fallback for installs with no kys password at all.

- Zihin haritasında ve kurulum sihirbazında merkez etiketi artık "OPENDELEGATE". / The mind map and the setup wizard now show "OPENDELEGATE" as the centre label.
- Bilgisayar kullanırken görev başında kısa bir plan yapılır; aynı mesaj üst üste gönderilmez, ekranda görünen öğe için yeniden arama yapılmaz. Test hata verince bırakılmaz (düzeltilip yeniden denenir); aynı araç bahanelerle tekrar tekrar başarısız olursa yaklaşım değiştirmesi istenir; iş yarımsa rapor "Bitmedi." ile başlar. / While using the computer a short plan is made first; the same message is not sent twice and a visible item is not searched for again. A failing test no longer stops the work (fix and retry); repeated failures of one tool trigger a change-of-approach warning; an unfinished job's report starts with "Bitmedi."
- "Bilgisayarda işlem yapıyorum" gibi boş bilgi satırları kaldırıldı; ne çalıştırıldığı söyleniyor. / Empty "performing an operation" info lines are gone; the assistant says what it is actually running.

### Eklendi / Added
- **Sohbetten telefona iş:** web sohbetinde "telefonda 7:30'a alarm kur", "feneri aç", "telefonu kilitle", "telefondan Ahmet'e WhatsApp yaz" ya da "telefonda şunu yap" denince OpenDelegate işi telefondaki uygulamaya verir (`telefonda_yap`), uygulama yapar ve sonucu sohbete döner. Uygulama sunucuyu uzun yoklamayla dinler (`GET /telefon/gorev/{token}?bekle=25`, sonuç `POST /telefon/sonuc/{token}`); sunucu yeni açıldıysa telefonun bağlanması 45 sn beklenir. Ödeme/banka işleri sesli uygulamadaki kapıdan geçer. Uygulama sürüm 4. / **Phone jobs from the chat:** ask in the web chat to set an alarm, turn on the torch, lock the phone, send a WhatsApp message or do anything on the phone; OpenDelegate hands the job to the phone app (`telefonda_yap`), the app does it and the result comes back to the chat. The app long-polls the server (`GET /telefon/gorev/{token}?bekle=25`, result via `POST /telefon/sonuc/{token}`); after a server restart it waits up to 45 s for the phone. Payment/bank actions go through the same guard as the voice app. App version 4.
- **Telefon paneli:** güç düğmesi OpenDelegate'i uygulamaya girmeden tamamen kapatır; panelde sadece "kapat" denirse panel kapanır ve tekrar dinlemez (model çağrılmaz), "telefonu kapat" ekranı kilitler. / **Phone panel:** a power button turns OpenDelegate off without opening the app; saying just "kapat" closes the panel and stops listening (no model call), while "telefonu kapat" locks the screen.
- **Uzak ekran (`/uzak`):** bilgisayar ekranını tarayıcıdan (telefon dahil) görüp tıklama, yazma, tuş ve kaydırma; otomatik tazelenir (ekran hareketliyken sık, durgunken seyrek; istenirse 1-5 sn ya da elle), iki parmakla yakınlaştırma, tam ekran ve sağdan açılan menü. Gri-siyah-beyaz piksel temalı, markalı arayüz (OPENDELEGATE ikonu ve yazısı), canlı durum göstergesi ve gecikme (ms). Canlı video değil. Adres bot/`OPENDELEGATE_SIR` tohumundan türeyen gizli yol + 256 bit token; adresi bilmek yetmez: sohbetteki biletli bağlantıyla ya da sohbette onay verilerek oturum açılır (oturum başlığı, çerezsiz), her bağlantıda cihaz ve IP ile haber verilir, `/uzakkapat` hepsini keser, CSP ve `frame-ancestors none` vardır. / **Remote screen (`/uzak`):** view the PC screen in a browser (phone included) and click, type, press keys and scroll; refreshes adaptively (often while the screen moves, rarely when idle; optionally 1-5 s or manual), pinch zoom, full screen and a right-side menu. Grey-black-white pixel-style branded interface (OPENDELEGATE icon and wordmark), live status indicator and latency (ms). Not live video. Hidden path plus 256-bit token derived from the bot/`OPENDELEGATE_SIR` seed; knowing the address is not enough: a session opens with the ticketed link from the chat or after approving in the chat (header-based session, no cookie), every connection reports device and IP, `/uzakkapat` cuts them all, CSP and `frame-ancestors none` apply.
- Web sohbetinde dosya, fotoğraf ve sesli mesaj yükleme (en fazla 25 MB, bellekte işlenir) ve mikrofon düğmesi. / File, photo and voice upload (up to 25 MB, processed in memory) and a microphone button in the web chat.
- Sohbet geçmişi yeniden açılışta ekranda görünür. / The chat history shows again after a restart.
- Web Push: sekme kapalıyken "Yeni mesaj" bildirimi (içerik taşımaz), PWA kurulumu için manifest ve ikonlar. Gerekli paket: `pywebpush`. / Web Push: a "new message" notification while the tab is closed (carries no text), a manifest and icons for PWA install. Required package: `pywebpush`.

### Düzeltildi / Fixed
- Telefonda ekrandan iş yaparken uygulamanın kendi dokunuşları paneli kapatıp işi yarıda bırakıyordu; iş sürerken panelin dışına dokunmak artık işi kesmez (durdur/kapat düğmesi çalışır). Telefonun "yapamadım" cevapları artık hata sayılır; üst üste iki başarısız telefon işinden sonra asistan durur ve sebebi söyler, ilgisiz denemelere sapmaz. / While working on the phone screen, the app's own taps closed the panel and cut the job short; tapping outside the panel no longer stops a running job (the stop/close buttons still do). The phone's "could not do it" replies now count as failures; after two failed phone jobs in a row the assistant stops and explains instead of wandering into unrelated attempts.
- Ana sohbet döngüsünde model düşünme modu kapalı (sağlayıcı reddederse eski yol) ve saat satırı tur başında sabit: DeepSeek önbelleği uzun işlerde her dakika boşa gitmiyor, cevaplar hızlanır. / The main chat loop now calls the model with thinking disabled (falls back if the provider refuses) and freezes the clock line per turn, so DeepSeek's prefix cache is not thrown away every minute on long jobs.
- Veritabanı sınırsız büyümüyor: eylem kaydındaki argümanlar 2.000 karaktere kırpılır, 90 günden eski eylemler haftalık raporla birlikte silinir (Neon ücretsiz 0,5 GB dolunca yeni hafıza sessizce yazılamazdı). / The database no longer grows without bound: action-log arguments are capped at 2,000 characters and actions older than 90 days are deleted with the weekly report (a full Neon free tier silently stopped new memory writes).
- Kayıt saatleri Türkiye saatiyle yazılır (sunucu UTC olduğu için 3 saat gerideydi; haftalık rapor ve yönetim sayfası buna göre yanılıyordu). / Record timestamps use Turkey time (the UTC server wrote them 3 hours behind; the weekly report and admin page were off accordingly).
- Aynı başarısız çağrının engellenmesi artık hata sayacına eklenmiyor (engel metni ile kontrol uyuşmuyordu). / A blocked repeat of a failed call no longer counts as a failure (the block text and its check did not match).
- Telefonda sohbetten gelen iş sürerken uyandırma cümlesi işi bölmez; iş sonucu sunucuya gönderilemezse bir kez daha denenir. / While a chat job runs on the phone, the wake phrase no longer interrupts it; a failed result upload is retried once.

### Güvenlik / Security
- `ag.py`: indirmede gerçekten bağlanılan IP de denetlenir; DNS rebinding ile iç ağa yönlendirilen isteğin cevabı okunmaz. Twilio arama yolundaki token log maskesine eklendi. `ag.py` ve `dsml.py` için testler eklendi. / `ag.py` now also checks the IP actually connected to; a DNS-rebinding redirect to the internal network is cut before the body is read. The Twilio call path token is added to the log mask. Tests added for `ag.py` and `dsml.py`.
- Model hesabının bakiyesi bitince (HTTP 402) ham hata yerine anlaşılır mesaj gösterilir. / When the model account runs out of balance (HTTP 402) a clear message is shown instead of the raw error.
- Asistan, ekran görüntüsü gelmediyse ya da araç hata verdiyse gördüğünü uydurmaz; "göremedim" der. / When no screenshot arrived or the tool failed, the assistant no longer invents what it sees; it says it could not see.

## [1.4.0] - 2026-11-26

### Değişti / Changed
- **Yeni ad: OpenDelegate.** README, rehberler, kurulum sihirbazı, asistanın kendini tanıtması, Android uygulama adı ve yönetim sayfası. Depo adı da OpenDelegate oldu. / **New name: OpenDelegate.** README, guides, setup wizard, the assistant's self-introduction, the Android app name and the admin page. The repository is renamed too.
- Android paket adı `com.opendelegate.app` oldu; bu yüzden eski uygulama güncellenmez, **yeniden kurulur** (önce eskisini silin). / The Android package name is now `com.opendelegate.app`, so the old app does not update; **install it fresh** (remove the old one first).
- Telefon uyandırma cümlesi "delegate bir bakar mısın" oldu. Sunucu eski "vekil" biçimini de ayıklar. / The phone wake phrase is now "delegate bir bakar mısın". The server still strips the old "vekil" form.
- Android ana ekran düğmesi artık Telegram botunu değil, kayıtlı sunucunun web sohbetini (`/sohbet`) açar. Önceki sürümlerde bu düğme geliştiricinin kendi test botunun adresini içeriyordu ve ayarlar penceresindeki örnek adres de geliştiricinin test sunucusuydu; ikisi de kaldırıldı. / The Android home button now opens the saved server's web chat (`/sohbet`) instead of a Telegram bot. In earlier versions this button contained the address of the developer's own test bot and the example address in the settings dialog was the developer's test server; both are removed.
- Render şablonu: servis adı `opendelegate`; `TELEGRAM_*` yerine `WEB_PAROLA`, `WEB_TOTP_SIR`, `OPENDELEGATE_SIR`. / Render blueprint: service name `opendelegate`; `WEB_PAROLA`, `WEB_TOTP_SIR`, `OPENDELEGATE_SIR` instead of `TELEGRAM_*`.

### Eklendi / Added
- **Web sohbeti (`/sohbet`):** Telegram'sız kullanım. Parola + doğrulama uygulaması (TOTP) girişi, deneme sınırı, güvenli çerez, CSRF ve CSP korumaları; cevaplar, onay düğmeleri, ekran görüntüleri ve dosyalar tarayıcıda. / **Web chat (`/sohbet`):** use without Telegram. Password + authenticator (TOTP) sign-in, attempt limits, secure cookie, CSRF and CSP protections; answers, approval buttons, screenshots and files in the browser.
- `OPENDELEGATE_SIR`: Telegram yokken türetilen anahtarların tohumu. / `OPENDELEGATE_SIR`: seed for derived keys when there is no Telegram.
- Kurulum sihirbazı: web parolası ve doğrulama uygulaması adımları; Telegram adımları kalktı. / Setup wizard: web password and authenticator steps; the Telegram steps are gone.
- Kurulum rehberi: kendi alan adı (Render Custom Domains) adımı. / Setup guide: own-domain step (Render Custom Domains).

### Kaldırıldı / Removed
- Telegram artık zorunlu değil; `TELEGRAM_BOT_TOKEN` boşsa tamamen kapalı. / Telegram is no longer required; it is completely off when `TELEGRAM_BOT_TOKEN` is empty.

## [1.3.0] - 2026-11-25

Android uygulaması 1.2.0 ile aynı; yalnızca sunucu tarafı değişti. / The Android app is the same as 1.2.0; only the server side changed.

### Eklendi / Added
- Salt okunur takvim: `TAKVIM_ICS_URL` (Google gizli iCal adresi). Telegram'da "bugün ne var" soruları ve sabah brifingindeki "Bugün" bölümü; tekrarlayan etkinlikler, tüm gün etkinlikleri, iptal ve değişen örnekler desteklenir. / Read-only calendar: `TAKVIM_ICS_URL` (Google secret iCal address). "What is on today" questions on Telegram and the "Today" section of the morning briefing; recurring events, all-day events, cancelled and moved instances are supported.

## [1.2.0] - 2026-11-24

### Değişti / Changed
- Android uygulaması: kırmızı vurgu yerine siyah ve koyu gri tema, vurgu rengi `#8A909A`. / Android app: black and dark grey theme instead of red, accent colour `#8A909A`.

### Eklendi / Added
- Yönetim sayfası (`/yonetim`): durum, hafıza sayıları, PC ajanı, son işler; gizli bağlantı, salt okuma. / Admin page (`/yonetim`): status, memory counts, PC agent, recent jobs; secret link, read-only.

## [1.1.0] - 2026-11-24

Android uygulaması 1.0.1 ile aynı; yalnızca sunucu tarafı değişti. / The Android app is the same as 1.0.1; only the server side changed.

### Eklendi / Added
- `/yedek`: kalıcı hafızayı JSON dosyası olarak dışa aktarır. / `/yedek`: exports the persistent memory as a JSON file.
- `DIL=en` ayarı: asistan cevapları, sesli okuma ve konuşma tanıma İngilizce. / `DIL=en` setting: English answers, spoken output and speech recognition.

## [1.0.1] - 2026-11-24

### Değişti / Changed
- Yeni Vekil ikonu: README, kurulum sihirbazı ve Android uygulaması. / New Vekil icon: README, setup wizard and the Android app.

### Eklendi / Added
- `eklentiler/` klasörü: müşteriye özel araçlar ana koda dokunmadan eklenir. / `eklentiler/` folder: customer-specific tools without touching the core code.
- Zihin haritası ve uygulama ekran görüntüleri README'de. / Mind map and app screenshots in the README.
- `render.yaml` ve README'de "Deploy to Render" düğmesi. / `render.yaml` and a "Deploy to Render" button in the README.
- İngilizce README ve kurulum rehberi, dil seçenek bağlantıları. / English README and setup guide, language links.
- SSS, gizlilik sayfası, yol haritası, değişiklik günlüğü. / FAQ, privacy page, roadmap, changelog.
- GitHub özel güvenlik bildirimi, Dependabot güvenlik güncellemeleri. / GitHub private vulnerability reporting, Dependabot security updates.

### Düzeltildi / Fixed
- Türkçe README'deki yazı animasyonu adresi kodlanmadığı için görünmüyordu. / The Turkish README typing animation did not show because its URL was not encoded.

## [1.0.0] - 2026-11-24

İlk açık kaynak sürüm: Telegram ve sesli asistan, bilgisayar ve telefon kontrolü, kalıcı hafıza, zihin haritası, kill switch, imzalı Android APK.
/ First open source release: Telegram and voice assistant, computer and phone control, persistent memory, mind map, kill switch, signed Android APK.
