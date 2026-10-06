/**
 * 介面字典（繁體中文 / 简体中文 / English / 日本語）。
 *
 * 引擎在 `i18n.js`，伺服器那一半在 `backend/services/i18n.py`。
 * 這一支只有資料，刻意沒有任何邏輯 —— 它是整個專案裡**最有可能被非工程師
 * 修改**的檔案（翻譯通常不是寫程式的人做的），所以它不該有東西會被改壞。
 *
 * ──────────────────────────────────────────────────────────────────
 * 這裡面**不會**出現的東西
 *
 * 歌名、歌星、客人取的暱稱、包廂名稱、櫃檯打上舞台的訊息、歌詞。
 * 它們是資料不是介面：歌名被翻掉的後果是使用者從此查不到那首歌，
 * 而且他會以為曲庫裡沒有。要把它們放進句子就用 {slot}
 * （「已加入：{title}」／"Added: {title}"），值原封不動塞回去。
 *
 * ──────────────────────────────────────────────────────────────────
 * emoji 留在字典裡（而不是留在 HTML 上）
 *
 * 看起來重複三次很浪費，但 `data-i18n` 換的是整個 textContent ——
 * emoji 留在 HTML 上會在第一次切語言時被洗掉。而且它本來就不是裝飾：
 * 包廂的燈是暗的，那顆圖示常常是老客人唯一在看的東西，
 * 所以它必須跟著字一起被當成「那顆按鈕的樣子」來管。
 *
 * ──────────────────────────────────────────────────────────────────
 * WIDTH_BUDGET：哪幾句話有寬度上限
 *
 * 「插播」兩個字，英文是 "Play Next" —— 顯示寬度 4 vs. 9，差一倍多。
 * 點歌台是觸控的，按鈕不能縮小，字撐出去就換行、推開隔壁的鍵，
 * 或被切成 "Play N…"。所以會擠的那幾顆帶一個半形寬度預算，
 * `frontend/tests/i18n.test.js` 把三種語言一起量過去。
 * 翻譯的人因此在 CI 就知道要改短，而不是等平板進了包廂才發現第二排少一顆鍵。
 *
 * 沒列在預算表裡的 key 代表它所在的位置可以換行（說明文字、提示列）。
 */

// 繁體中文是基準：每一個 key 都要在這裡出現，其他語言以它為準
// （frontend/tests/i18n.test.js 擋缺字）。
const I18N_ZH_TW = {
  // --- 最上排 ---
  // 計時、續時、包廂切換、輪唱、額度、櫃檯鎖那幾顆**只有 _hint**（滑鼠提示），
  // 沒有鍵面文字：它們的字是 render 當場寫進去的（狀態就寫在字上，
  // 「⏱️ 開始計時」會變成「⏱️ 1:23:45」），標了 data-i18n 也會在第一次
  // render 時連標記一起被蓋掉 —— 留著只會讓人以為那幾顆翻好了。
  // 滑鼠提示不會被覆蓋，所以那一半照常跟著語言走。
  // 它們的鍵面文字要跟著語言走，得等動態訊息那一輪（見 docs/ROADMAP.md）。
  "header.room_clock_hint": "歡唱時間。還沒開始計時就按一下開始；計時中按一下可以結束計時",
  "header.room_extend_hint": "續時：加時間，不是重開一場。時間到停住時按它會接著播下一首",
  "header.room_pause_hint": "暫停計時（中場休息、餐點來了）。播放不受影響",
  "header.marquee": "📺 舞台訊息",
  "header.marquee_hint": "把一句話打到舞台螢幕上（餐點到了、生日祝福）",
  "header.service": "🔔 服務鈴",
  "header.service_hint": "叫櫃檯：送餐、加冰塊、清潔、麥克風／音響、結帳",
  "header.room_switch_hint": "這一頁現在在哪一間包廂。按一下看全店總覽並切換",
  "header.nickname": "設定暱稱",
  "header.nickname_hint": "設定顯示在佇列上的暱稱，讓大家知道是誰點的歌",
  "header.open_stage": "🖥️ 開啟舞台大螢幕",
  "header.qr": "📱 手機點歌 QR",
  "header.settings": "⚙️ 系統設定",
  "header.settings_hint": "開機預設音效、自動音量平衡、快取上限、AI 模型",
  "header.staff_lock_hint": "櫃檯管理鎖",
  "header.language_hint": "這台裝置的介面語言。舞台螢幕的語言在系統設定頁裡改（那是整間包廂共用的）",

  // --- 搜尋與曲庫分頁 ---
  "search.placeholder": "輸入歌曲名稱、歌手、或貼上 YouTube / YouTube Music 網址...",
  "search.button": "搜尋",
  "lib.cached": "📚 快取歌曲",
  "lib.browse": "🎼 分類瀏覽",
  "lib.browse_hint": "依語言別與歌手分類點歌",
  "lib.find": "🔤 注音查歌",
  "lib.find_hint": "只在已備好的曲庫裡查歌：注音首字、歌名字數，查到的每一首都是快取秒播",
  "lib.numbers": "🔢 歌號點歌",
  "lib.numbers_hint": "歌號點歌：直接打六位數歌號（每首歌的號碼印在歌卡上，而且永遠不會變成別首歌）",
  "lib.artists": "🎤 歌星查歌",
  "lib.artists_hint": "歌星查歌：按歌星名字的注音首字（周杰倫＝ㄓㄐㄌ），選一位就翻開他的歌單",
  "lib.new": "🆕 新歌推薦",
  "lib.new_hint": "最近加入曲庫的新歌與為你推薦的歌單",
  "lib.favorites": "⭐ 我的最愛",
  "lib.rankings": "🏆 點唱排行",
  "lib.history": "🕘 已唱歷史",
  "lib.voice": "🎤 我的音域",
  "lib.voice_hint": "我的音域：機器從你唱過的歌認識你的聲音，之後對每一首歌說「這首對你偏高，建議降 2 個 Key」",
  "lib.trends": "📊 我的成績",
  "lib.trends_hint": "跨場次段落趨勢：同一首唱滿三次就看得出你一向強在哪一段",
  "lib.contest": "🥇 今晚擂台",
  "lib.contest_hint": "今晚擂台：這一場誰是歌王。唱滿三首不同的歌就上榜",
  "lib.recordings": "🎙️ 錄唱回放",
  "lib.recordings_hint": "錄唱回放：把剛剛唱的那一次聽回來（要先到系統設定頁打開錄音）",
  "lib.cache": "🗂️ 快取管理",
  "lib.batch": "🌙 排程預處理",
  "lib.batch_hint": "半夜自動把整張播放清單處理好，隔天點下去就是秒播",
  "lib.rooms": "🏠 包廂",
  "lib.rooms_hint": "多包廂：一台伺服器帶多組舞台與佇列。這一頁是櫃檯總覽",
  "lib.import": "📁 本機匯入",
  "lib.import_hint": "本機匯入：把自己的伴唱影片或音檔放進 cache/import/，跑同一條流水線變成曲庫裡的歌",

  // --- 佇列 ---
  "queue.title": "📋 點歌排隊佇列",
  "queue.rotation_hint": "公平輪唱：新點的歌照「這是誰的第幾首」排，讓大家輪流唱",
  "queue.quota_down_hint": "每人待唱上限減一（減到 0 就是不限）",
  "queue.quota_up_hint": "每人待唱上限加一",
  "queue.quota_hint": "每人同時最多能排幾首待唱。按一下在「不限」與上次的上限之間切換",
  "queue.rotation_empty": "還沒有人點歌",
  "queue.rotation_reset": "重新排",
  "queue.rotation_reset_hint": "把「誰唱過幾首」歸零（換一批客人時用）。佇列不動",
  "queue.autofill_idle": "想不到要唱什麼？讓機器從曲庫裡挑一首",
  "queue.random_pick": "🎲 來一首",
  "queue.random_pick_hint": "從已經備好的曲庫隨機點一首（算你點的，會佔額度與輪序）",

  // --- 控制列 ---
  "deck.restart": "🔄 重唱",
  "deck.restart_hint": "重新開始",
  "deck.play": "▶ 播放",
  "deck.pause": "⏸ 暫停",
  "deck.skip": "⏭ 切歌",
  "deck.skip_hint": "切歌 / 下一首",
  "deck.idle_title": "尚未播放歌曲",
  "deck.idle_artist": "請從上方搜尋點歌",
  "deck.seek_hint": "點一下跳到該處播放",

  // --- 舞台 ---
  "stage.unlock_title": "點擊螢幕啟用 KTV 伴唱音效與麥克風",
  "stage.unlock_hint": "點擊後即可解鎖瀏覽器聲音播放與麥克風即時評分！",
  "stage.idle_title": "KaraTube 伴唱系統",
  "stage.idle_artist": "請在點歌台搜尋並點播歌曲",
  "stage.interlude": "間奏",
  "stage.practice": "練唱循環",
  "stage.guide_duck": "導唱自動淡出",
  "stage.mic_agc": "麥克風自動增益",
  "stage.feedback_guard": "防嘯叫",
  "stage.harmony": "和聲",
  "stage.pitch_fix": "修音",
  "stage.recording": "錄音中",
  "stage.finale": "🎤 歡唱時間結束",
};

/**
 * 簡體中文。**不是**把繁體逐字換成簡體 —— 那樣做出來的是「快取歌曲」「螢幕」
 * 「佇列」「預設」，每一個字都是簡體，整句卻是台灣話；對岸的使用者一眼
 * 看得出這是機器轉的，而且「快取」「佇列」他真的要停下來想一下是什麼。
 * 所以這一份是照用詞重寫的（缓存、屏幕、队列、默认、视频、本地、二维码），
 * `frontend/tests/i18n.test.js` 有兩條守衛：不准殘留繁體字，也不准出現
 * 台灣用詞（寫成簡體字的「影片」「萤幕」也算）。
 *
 * 跟繁中一字不差的那幾句（「⏭ 切歌」「修音」）是兩岸本來就同形的字，
 * 它們要被**列名**在測試的白名單裡 —— 不然「漏翻、直接抄繁中」跟「本來就
 * 一樣」在完整性測試裡長得一模一樣。
 *
 * 只有一件事刻意**沒有**在地化：注音查歌。這台機器的首字查歌用的是
 * 注音符號（ㄅㄆㄇ），對岸的使用者打的是拼音首字母 —— 鍵名照實寫「注音」，
 * 說明裡講清楚它不是拼音。把它改名叫「拼音查歌」會讓人按著 Z J L 找周杰伦，
 * 然後以為曲庫裡沒有。拼音首字母查歌見 docs/ROADMAP.md。
 */
const I18N_ZH_CN = {
  "header.room_clock_hint": "欢唱时间。还没开始计时就点一下开始；计时中点一下可以结束计时",
  "header.room_extend_hint": "续时：加时间，不是重开一场。时间到停住时点它会接着播下一首",
  "header.room_pause_hint": "暂停计时（中场休息、餐点来了）。播放不受影响",
  "header.marquee": "📺 大屏消息",
  "header.marquee_hint": "把一句话发到大屏幕上（餐点到了、生日祝福）",
  "header.service": "🔔 呼叫服务",
  "header.service_hint": "呼叫前台：送餐、加冰块、清洁、麦克风／音响、结账",
  "header.room_switch_hint": "这一页现在在哪个包厢。点一下查看全店总览并切换",
  "header.nickname": "设置昵称",
  "header.nickname_hint": "设置显示在队列里的昵称，让大家知道是谁点的歌",
  "header.open_stage": "🖥️ 打开大屏幕",
  "header.qr": "📱 扫码点歌",
  "header.settings": "⚙️ 系统设置",
  "header.settings_hint": "开机默认音效、自动音量平衡、缓存上限、AI 模型",
  "header.staff_lock_hint": "前台管理锁",
  "header.language_hint": "这台设备的界面语言。大屏幕的语言在系统设置页里改（那是整个包厢共用的）",

  "search.placeholder": "输入歌名、歌手，或粘贴 YouTube / YouTube Music 链接...",
  "search.button": "搜索",
  "lib.cached": "📚 缓存歌曲",
  "lib.browse": "🎼 分类浏览",
  "lib.browse_hint": "按语种与歌手分类点歌",
  "lib.find": "🔤 注音查歌",
  "lib.find_hint": "只在已备好的曲库里查歌：按注音符号首字（ㄅㄆㄇ，不是拼音字母）、歌名字数，查到的每一首都是缓存秒播",
  "lib.numbers": "🔢 歌号点歌",
  "lib.numbers_hint": "歌号点歌：直接输入六位歌号（每首歌的号码印在歌曲卡片上，而且永远不会变成别的歌）",
  "lib.artists": "🎤 歌手查歌",
  "lib.artists_hint": "歌手查歌：按歌手名字的注音首字（周杰伦＝ㄓㄐㄌ，不是拼音字母），选一位就打开他的歌单",
  "lib.new": "🆕 新歌推荐",
  "lib.new_hint": "最近加入曲库的新歌与为你推荐的歌单",
  "lib.favorites": "⭐ 我的收藏",
  "lib.rankings": "🏆 点唱排行",
  "lib.history": "🕘 已唱记录",
  "lib.voice": "🎤 我的音域",
  "lib.voice_hint": "我的音域：机器从你唱过的歌认识你的声音，之后对每一首歌说“这首对你偏高，建议降 2 个 Key”",
  "lib.trends": "📊 我的成绩",
  "lib.trends_hint": "跨场次段落趋势：同一首唱满三次就看得出你一向强在哪一段",
  "lib.contest": "🥇 今晚擂台",
  "lib.contest_hint": "今晚擂台：这一场谁是歌王。唱满三首不同的歌就上榜",
  "lib.recordings": "🎙️ 录音回放",
  "lib.recordings_hint": "录音回放：把刚才唱的那一遍听回来（要先在系统设置页打开录音）",
  "lib.cache": "🗂️ 缓存管理",
  "lib.batch": "🌙 定时预处理",
  "lib.batch_hint": "半夜自动把整个播放列表处理好，第二天一点就是秒播",
  "lib.rooms": "🏠 包厢",
  "lib.rooms_hint": "多包厢：一台服务器带多组大屏与队列。这一页是前台总览",
  "lib.import": "📁 本地导入",
  "lib.import_hint": "本地导入：把自己的伴奏视频或音频文件放进 cache/import/，走同一条流水线变成曲库里的歌",

  "queue.title": "📋 点歌队列",
  "queue.rotation_hint": "公平轮唱：新点的歌按“这是谁的第几首”排，让大家轮流唱",
  "queue.quota_down_hint": "每人待唱上限减一（减到 0 就是不限）",
  "queue.quota_up_hint": "每人待唱上限加一",
  "queue.quota_hint": "每人同时最多能排几首待唱。点一下在“不限”与上次的上限之间切换",
  "queue.rotation_empty": "还没有人点歌",
  "queue.rotation_reset": "重新排",
  "queue.rotation_reset_hint": "把“谁唱过几首”清零（换一批客人时用）。队列不动",
  "queue.autofill_idle": "想不到唱什么？让机器从曲库里挑一首",
  "queue.random_pick": "🎲 来一首",
  "queue.random_pick_hint": "从已经备好的曲库随机点一首（算你点的，会占额度与轮序）",

  "deck.restart": "🔄 重唱",
  "deck.restart_hint": "重新开始",
  "deck.play": "▶ 播放",
  "deck.pause": "⏸ 暂停",
  "deck.skip": "⏭ 切歌",
  "deck.skip_hint": "切歌 / 下一首",
  "deck.idle_title": "尚未播放歌曲",
  "deck.idle_artist": "请从上方搜索点歌",
  "deck.seek_hint": "点一下跳到该处播放",

  "stage.unlock_title": "点击屏幕启用 KTV 伴唱音效与麦克风",
  "stage.unlock_hint": "点击后即可解锁浏览器声音播放与麦克风实时评分！",
  "stage.idle_title": "KaraTube 伴唱系统",
  "stage.idle_artist": "请在点歌台搜索并点播歌曲",
  "stage.interlude": "间奏",
  "stage.practice": "练唱循环",
  "stage.guide_duck": "导唱自动淡出",
  "stage.mic_agc": "麦克风自动增益",
  "stage.feedback_guard": "防啸叫",
  "stage.harmony": "和声",
  "stage.pitch_fix": "修音",
  "stage.recording": "录音中",
  "stage.finale": "🎤 欢唱时间结束",
};

const I18N_EN = {
  "header.room_clock_hint": "Room time. Tap to start the session clock; tap again while running to end it",
  "header.room_extend_hint": "Extend: adds time, does not start a new session. If time is up, this resumes the next song",
  "header.room_pause_hint": "Pause the clock (break, food arriving). Playback is unaffected",
  "header.marquee": "📺 Message",
  "header.marquee_hint": "Put a line of text on the stage screen (food is here, birthday wishes)",
  "header.service": "🔔 Call Staff",
  "header.service_hint": "Call the front desk: food, ice, cleaning, mic / audio, check out",
  "header.room_switch_hint": "Which room this console is showing. Tap for the front-desk overview and to switch",
  "header.nickname": "Set Name",
  "header.nickname_hint": "The name shown on the queue, so everyone knows who picked the song",
  "header.open_stage": "🖥️ Open Stage",
  "header.qr": "📱 Phone QR",
  "header.settings": "⚙️ Settings",
  "header.settings_hint": "Startup audio defaults, loudness matching, cache limits, AI models",
  "header.staff_lock_hint": "Staff lock",
  "header.language_hint": "Interface language for this device. The stage screen's language is in Settings (it is shared by the whole room)",

  "search.placeholder": "Song title, artist, or paste a YouTube / YouTube Music link...",
  "search.button": "Search",
  "lib.cached": "📚 Library",
  "lib.browse": "🎼 Browse",
  "lib.browse_hint": "Browse by language and artist",
  "lib.find": "🔤 Keypad",
  "lib.find_hint": "Search the ready library only: Zhuyin initials and title length. Every hit plays instantly",
  "lib.numbers": "🔢 Number",
  "lib.numbers_hint": "Song numbers: type the six-digit code (printed on each song card, and never reused for another song)",
  "lib.artists": "🎤 Artist",
  "lib.artists_hint": "Find by artist: type the Zhuyin initials of the name, then open that artist's songs",
  "lib.new": "🆕 New",
  "lib.new_hint": "Recently added songs and picks recommended for you",
  "lib.favorites": "⭐ Favorites",
  "lib.rankings": "🏆 Top Played",
  "lib.history": "🕘 History",
  "lib.voice": "🎤 My Range",
  "lib.voice_hint": "My vocal range: the machine learns your voice from what you sing, then tells you \"this one sits high for you, try −2 keys\"",
  "lib.trends": "📊 My Stats",
  "lib.trends_hint": "Section trends across nights: sing the same song three times and your strongest section shows up",
  "lib.contest": "🥇 Tonight",
  "lib.contest_hint": "Tonight's leaderboard. Three different songs puts you on the board",
  "lib.recordings": "🎙️ Replays",
  "lib.recordings_hint": "Play back the take you just sang (turn recording on in Settings first)",
  "lib.cache": "🗂️ Storage",
  "lib.batch": "🌙 Overnight",
  "lib.batch_hint": "Process a whole playlist overnight so it plays instantly the next day",
  "lib.rooms": "🏠 Rooms",
  "lib.rooms_hint": "Multi-room: one server, many stages and queues. This page is the front-desk overview",
  "lib.import": "📁 Import",
  "lib.import_hint": "Local import: drop your own karaoke video or audio into cache/import/ and run the same pipeline",

  "queue.title": "📋 Song Queue",
  "queue.rotation_hint": "Fair rotation: new picks are ordered by \"whose nth song is this\", so everyone takes turns",
  "queue.quota_down_hint": "Lower the per-person pending limit (0 means unlimited)",
  "queue.quota_up_hint": "Raise the per-person pending limit",
  "queue.quota_hint": "How many songs one person may have waiting at once. Tap to toggle between unlimited and your last limit",
  "queue.rotation_empty": "Nobody has picked a song yet",
  "queue.rotation_reset": "Reset",
  "queue.rotation_reset_hint": "Reset \"who has sung how many\" (for a new group). The queue is untouched",
  "queue.autofill_idle": "Not sure what to sing? Let the machine pick one from the library",
  "queue.random_pick": "🎲 Surprise",
  "queue.random_pick_hint": "Pick a random song from the ready library (counts as yours, uses your quota and turn)",

  "deck.restart": "🔄 Restart",
  "deck.restart_hint": "Start this song over",
  "deck.play": "▶ Play",
  "deck.pause": "⏸ Pause",
  "deck.skip": "⏭ Skip",
  "deck.skip_hint": "Skip / next song",
  "deck.idle_title": "Nothing playing",
  "deck.idle_artist": "Search above to add a song",
  "deck.seek_hint": "Tap to jump to that point",

  "stage.unlock_title": "Tap the screen to enable karaoke audio and the microphone",
  "stage.unlock_hint": "Tapping unlocks browser audio playback and live mic scoring!",
  "stage.idle_title": "KaraTube Karaoke",
  "stage.idle_artist": "Search and queue a song on the console",
  "stage.interlude": "Interlude",
  "stage.practice": "Loop",
  "stage.guide_duck": "Guide auto-fade",
  "stage.mic_agc": "Mic auto-gain",
  "stage.feedback_guard": "Feedback guard",
  "stage.harmony": "Harmony",
  "stage.pitch_fix": "Pitch fix",
  "stage.recording": "Recording",
  "stage.finale": "🎤 Session over",
};

const I18N_JA = {
  "header.room_clock_hint": "利用時間。未開始ならタップで開始、計測中にタップすると終了します",
  "header.room_extend_hint": "延長：時間を追加します（セッションのやり直しではありません）。時間切れで停止中なら次の曲が流れます",
  "header.room_pause_hint": "計測を一時停止（休憩、料理の到着）。再生には影響しません",
  "header.marquee": "📺 メッセージ",
  "header.marquee_hint": "ステージ画面に一言表示します（料理の到着、誕生日のお祝い）",
  "header.service": "🔔 呼び出し",
  "header.service_hint": "フロントを呼ぶ：料理、氷、清掃、マイク／音響、会計",
  "header.room_switch_hint": "この画面が今どの部屋を表示しているか。タップで全体一覧と切り替え",
  "header.nickname": "名前設定",
  "header.nickname_hint": "予約リストに表示される名前。誰が入れた曲か分かります",
  "header.open_stage": "🖥️ ステージ画面",
  "header.qr": "📱 スマホ用QR",
  "header.settings": "⚙️ 設定",
  "header.settings_hint": "起動時の音響初期値、音量そろえ、キャッシュ上限、AIモデル",
  "header.staff_lock_hint": "スタッフロック",
  "header.language_hint": "この端末の表示言語。ステージ画面の言語は設定ページにあります（部屋全体で共有）",

  "search.placeholder": "曲名、アーティスト、または YouTube / YouTube Music のURLを貼り付け...",
  "search.button": "検索",
  "lib.cached": "📚 ライブラリ",
  "lib.browse": "🎼 分類",
  "lib.browse_hint": "言語別・アーティスト別に探す",
  "lib.find": "🔤 文字検索",
  "lib.find_hint": "準備済みライブラリだけを検索：注音の頭文字と曲名の字数。ヒットした曲はすぐ再生できます",
  "lib.numbers": "🔢 曲番号",
  "lib.numbers_hint": "曲番号で予約：6桁の番号を入力（番号は曲カードに印字され、他の曲に変わることはありません）",
  "lib.artists": "🎤 歌手検索",
  "lib.artists_hint": "歌手から探す：名前の注音の頭文字を入力し、その歌手の曲一覧を開きます",
  "lib.new": "🆕 新着",
  "lib.new_hint": "最近追加された曲と、あなたへのおすすめ",
  "lib.favorites": "⭐ お気に入り",
  "lib.rankings": "🏆 人気順",
  "lib.history": "🕘 履歴",
  "lib.voice": "🎤 音域",
  "lib.voice_hint": "あなたの音域：歌った曲から声を学び、曲ごとに「これは高めです。2キー下げては」と提案します",
  "lib.trends": "📊 成績",
  "lib.trends_hint": "回をまたいだ区間の傾向：同じ曲を3回歌うと、得意な区間が見えてきます",
  "lib.contest": "🥇 今夜の王",
  "lib.contest_hint": "今夜のランキング。違う曲を3曲歌うとランク入りします",
  "lib.recordings": "🎙️ 録音再生",
  "lib.recordings_hint": "さっき歌ったテイクを聴き返す（先に設定ページで録音をオンに）",
  "lib.cache": "🗂️ 保存管理",
  "lib.batch": "🌙 夜間処理",
  "lib.batch_hint": "夜のうちにプレイリストをまとめて処理し、翌日はすぐ再生できるようにします",
  "lib.rooms": "🏠 ルーム",
  "lib.rooms_hint": "複数ルーム：1台のサーバーで複数のステージと予約リスト。このページはフロント用の一覧です",
  "lib.import": "📁 取り込み",
  "lib.import_hint": "ローカル取り込み：自分のカラオケ映像や音源を cache/import/ に置くと同じ処理を通ります",

  "queue.title": "📋 予約リスト",
  "queue.rotation_hint": "公平ローテーション：新しい予約を「その人の何曲目か」で並べ、順番に回します",
  "queue.quota_down_hint": "1人あたりの待ち曲数の上限を1つ減らす（0は無制限）",
  "queue.quota_up_hint": "1人あたりの待ち曲数の上限を1つ増やす",
  "queue.quota_hint": "1人が同時に待たせられる曲数。タップで「無制限」と前回の上限を切り替えます",
  "queue.rotation_empty": "まだ予約がありません",
  "queue.rotation_reset": "リセット",
  "queue.rotation_reset_hint": "「誰が何曲歌ったか」をリセット（客が入れ替わったとき）。予約リストはそのままです",
  "queue.autofill_idle": "何を歌うか迷ったら、ライブラリから1曲選ばせましょう",
  "queue.random_pick": "🎲 おまかせ",
  "queue.random_pick_hint": "準備済みライブラリからランダムに1曲（自分の予約として、上限と順番を消費します）",

  "deck.restart": "🔄 最初から",
  "deck.restart_hint": "この曲を最初から",
  "deck.play": "▶ 再生",
  "deck.pause": "⏸ 一時停止",
  "deck.skip": "⏭ 次の曲",
  "deck.skip_hint": "スキップ／次の曲",
  "deck.idle_title": "再生していません",
  "deck.idle_artist": "上の検索から曲を予約してください",
  "deck.seek_hint": "タップでその位置から再生",

  "stage.unlock_title": "画面をタップしてカラオケ音声とマイクを有効にします",
  "stage.unlock_hint": "タップすると音声再生とマイクのリアルタイム採点が使えるようになります！",
  "stage.idle_title": "KaraTube カラオケ",
  "stage.idle_artist": "操作画面から曲を検索して予約してください",
  "stage.interlude": "間奏",
  "stage.practice": "練習ループ",
  "stage.guide_duck": "ガイド自動フェード",
  "stage.mic_agc": "マイク自動調整",
  "stage.feedback_guard": "ハウリング抑制",
  "stage.harmony": "ハモリ",
  "stage.pitch_fix": "音程補正",
  "stage.recording": "録音中",
  "stage.finale": "🎤 歌唱時間終了",
};

/**
 * 有寬度上限的 key（單位：半形寬，全形與 emoji 算 2）。
 *
 * 數字是從現有版面量出來的，而不是「看起來差不多」：
 *   * 曲庫分頁（lib.*）—— 一排十幾顆橫向排列的鍵，預算 14。
 *     中文「📚 快取歌曲」是 11，英文 "📚 Library" 是 10，日文
 *     「📚 ライブラリ」是 14 —— 剛好踩到線，而那正是這張表存在的理由。
 *   * 最上排（header.*）—— 跟其他六七顆鍵擠同一行，預算 16。
 *   * 控制列（deck.*）—— 三顆主鍵固定寬，預算 12。
 *   * 舞台徽章（stage.* 徽章）—— 疊在畫面右側，預算 20。
 *
 * 說明文字（*_hint，滑鼠提示）、提示列那幾句話沒有上限：它們本來就會換行。
 */
const I18N_WIDTH_BUDGET = {
  "header.marquee": 16,
  "header.service": 16,
  "header.nickname": 14,
  "header.open_stage": 20,
  "header.qr": 16,
  "header.settings": 14,
  "search.button": 10,
  "lib.cached": 14, "lib.browse": 14, "lib.find": 14, "lib.numbers": 14,
  "lib.artists": 14, "lib.new": 14, "lib.favorites": 14, "lib.rankings": 14,
  "lib.history": 14, "lib.voice": 14, "lib.trends": 14, "lib.contest": 14,
  "lib.recordings": 14, "lib.cache": 14, "lib.batch": 14, "lib.rooms": 14,
  "lib.import": 14,
  "queue.title": 18,
  "queue.rotation_reset": 12,
  "queue.random_pick": 14,
  "deck.restart": 12, "deck.play": 12, "deck.pause": 12, "deck.skip": 12,
  "stage.practice": 20, "stage.guide_duck": 20, "stage.mic_agc": 20,
  "stage.feedback_guard": 20, "stage.harmony": 20, "stage.pitch_fix": 20,
  "stage.recording": 12, "stage.finale": 24,
};

const I18N_CATALOGS = { "zh-TW": I18N_ZH_TW, "zh-CN": I18N_ZH_CN, "en": I18N_EN, "ja": I18N_JA };

// 語言選單上的名字。跟 backend/services/i18n.py 的 LOCALES 是同一份內容
// （tests/test_i18n.py 把兩邊釘在一起）—— 離線開著的舞台也要列得出來，
// 所以前端不能只靠 API 才知道有哪些語言。
const I18N_LOCALES = [
  { code: "zh-TW", name: "繁體中文", flag: "🇹🇼" },
  { code: "zh-CN", name: "简体中文", flag: "🇨🇳" },
  { code: "en", name: "English", flag: "🇬🇧" },
  { code: "ja", name: "日本語", flag: "🇯🇵" },
];

if (typeof window !== "undefined") {
  window.I18nCatalog = {
    catalogs: I18N_CATALOGS,
    locales: I18N_LOCALES,
    widthBudget: I18N_WIDTH_BUDGET,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    I18N_CATALOGS, I18N_LOCALES, I18N_WIDTH_BUDGET,
    I18N_ZH_TW, I18N_ZH_CN, I18N_EN, I18N_JA,
  };
}
