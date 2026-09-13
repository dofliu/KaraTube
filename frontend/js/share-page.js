/**
 * 分享頁的畫面接線 (Share Page)
 *
 * 這一頁跟系統其他頁面不一樣：打開它的人**不在包廂裡**，手上只有一個網址，
 * 而且很可能是在手機上、用行動網路點開的。所以這一頁：
 *
 *   * 只認網址裡的 token，不需要任何設定或身分；
 *   * 所有文字一律用 textContent 塞進去 —— 歌名是從 YouTube 抓回來的字串，
 *     用 innerHTML 就等於把別人取的標題當程式碼跑；
 *   * 打不開時要講**為什麼**（過期／撤銷／次數用完／根本連不到這個網路），
 *     一句「載入失敗」會讓人以為是自己手機的問題而一直重整。
 */
(function () {
  const V = window.ShareView;
  const el = (id) => document.getElementById(id);

  const token = V.shareTokenFromLocation(window.location);

  /** 失效／出錯時的畫面：把卡片收掉，只留一句話跟一個說明。 */
  function showError(message) {
    el("shareCard").hidden = true;
    el("shareError").hidden = false;
    el("shareErrorText").textContent = message;
  }

  /**
   * 讓錄音的進度條拖得動。
   *
   * MediaRecorder 產生的 webm 標頭裡沒有 Duration（串流容器，開始錄的時候
   * 還不知道會錄多久），瀏覽器因此把 duration 當成 Infinity，進度條變成
   * 一條拖不動的線。先 seek 到一個不可能的時間點逼它算出長度，再跳回 0。
   * 分享頁比後台更需要這個：客人拿到連結第一件事就是拖到副歌。
   */
  function fixShareDuration(audio) {
    if (!audio || audio.dataset.durationFixed || audio.duration !== Infinity) return;
    audio.dataset.durationFixed = "1";
    const onChange = () => {
      if (!Number.isFinite(audio.duration)) return;
      audio.removeEventListener("durationchange", onChange);
      audio.currentTime = 0;
    };
    audio.addEventListener("durationchange", onChange);
    audio.currentTime = 1e101;
  }

  /**
   * 倒數。每 30 秒重寫一次那句話，而不是每秒 ——
   * 講的是「還有 3 小時」，每秒重畫一次只是讓手機多耗電。
   */
  function startCountdown(seconds) {
    let left = Math.max(0, Number(seconds) || 0);
    const line = el("shareExpiry");
    // timer 要先宣告：第一次 tick() 是在 setInterval 之前跑的（連結一進來
    // 就已經過期時要立刻收掉畫面），那時候用 const 會踩到暫時性死區。
    let timer = null;
    const tick = () => {
      line.textContent = V.expiryPhrase(left);
      line.classList.toggle("is-urgent", V.expiryIsUrgent(left));
      if (left <= 0) {
        // 過期的那一刻就把播放器收掉：繼續讓人按下去只會拿到 410，
        // 而畫面上還寫著「已過期」，看起來像系統壞了。
        showError("這個分享連結已經過期了，請原點歌的人重新分享一次。");
        clearInterval(timer);
      }
      left -= 30;
    };
    tick();
    if (left > 0) timer = setInterval(tick, 30000);
  }

  function render(data) {
    const rec = data.recording || {};
    const share = data.share || {};

    document.title = `${rec.title || "這一次的演唱"} ・ KaraTube`;
    el("shareTitle").textContent = rec.title || "這一次的演唱";
    el("shareMeta").textContent = V.shareMetaLine(rec);

    const scoreLine = V.shareScoreLine(rec);
    el("shareScore").textContent = scoreLine;
    el("shareScore").hidden = !scoreLine;

    if (rec.thumbnail) {
      const img = el("shareThumb");
      img.src = rec.thumbnail;
      img.hidden = false;
      img.onerror = () => { img.hidden = true; };
    }

    const audio = el("shareAudio");
    audio.src = data.audio_url;
    audio.addEventListener("loadedmetadata", () => fixShareDuration(audio));

    el("shareDownload").href = data.download_url;

    if (share.downloads_left !== null && share.downloads_left !== undefined) {
      el("shareDownloads").textContent = `還可以下載 ${share.downloads_left} 次`;
      el("shareDownloads").hidden = false;
    }

    startCountdown(share.expires_in_seconds);
    el("shareCard").hidden = false;
  }

  async function load() {
    if (!token) {
      showError(V.shareErrorMessage(404, ""));
      return;
    }
    try {
      const res = await fetch(`/api/share/${encodeURIComponent(token)}`);
      if (!res.ok) {
        // detail 是伺服器給的那一句（過期／撤銷／次數用完各有各的說法）。
        // 解不出 JSON 就交給狀態碼去猜，不要把解析錯誤丟到畫面上。
        let detail = "";
        try {
          detail = (await res.json()).detail || "";
        } catch (e) { /* 不是 JSON（例如反向代理擋掉了），用狀態碼的說法 */ }
        showError(V.shareErrorMessage(res.status, detail));
        return;
      }
      render(await res.json());
    } catch (e) {
      showError(V.shareErrorMessage(0, ""));
    }
  }

  load();
})();
