"""
每人待唱上限（點歌額度）

公平輪唱解掉的是**順序**：一個人連點五首，其他人不必等完那五首。但它沒有解掉
另一半 —— 那五首還是在佇列裡。包廂裡十個人、佇列裡二十首歌、其中十二首是同一
個人點的，這時候輪序只是把那十二首攤開來排，散場前佇列還是清不完，而且後面
那幾首的點歌者早就走了（歌照唱，沒人上台）。

所以這一支管的是**量**，不是順序：一個人同時最多能有幾首歌在等。

ROADMAP 把這一項掛著沒做，掛的理由寫得很清楚：「要先想清楚『滿了之後怎麼辦』」。
下面六個決定就是那一題的答案。

決定一：上限算的是「待唱」，不是「今晚總共唱幾首」
    「今晚最多唱五首」這種額度沒有好的收尾。有人第五首唱完，麥克風還在他手上，
    機器對著一個站在包廂中間的人說「你不能再點了」—— 而且是**永久**的，
    今晚剩下的三個小時都不行，除非有人去後台按重設。這不是規則，是懲罰。

    「同時最多排三首」則自己會解開：排滿了就等其中一首唱完，唱完那一格就空出來。
    機器講得出「什麼時候可以再點」，而且那個時間點馬上就到（就是下一首唱完）。
    沒有人需要去後台，也沒有人被鎖在今晚之外。

    而且它才是包廂真正在抱怨的那件事。沒有人會說「你今晚唱太多首」——
    時間還夠就多唱幾首，本來就沒關係；大家會說的是「佇列整排都是你的歌」。

決定二：沒取暱稱的那一桶照樣算一份額度（而且刻意就是一份）
    輪唱把所有沒取暱稱的人算成**同一位**。額度沿用同一個身分定義，於是
    「沒取暱稱」＝ 整桶共用一份額度。

    這件事乍看很粗糙，其實是這個功能唯一沒有漏洞的寫法。反過來寫（沒取名的
    不受限）看起來比較體貼，但它等於公告「把暱稱刪掉就無限點」—— 額度會在
    第一個發現這件事的人手上當場失效。

    照這個寫法，刪掉暱稱是把自己丟進一個**已經被別人塞滿**的桶子裡，嚴格
    比較差；取暱稱才拿得到屬於自己的三首。額度因此是唯一一個「取暱稱有好處」
    的功能（輪唱只是讓你排得比較公平），拒絕的那句話也就直接講出解法：
    「取個暱稱就有自己的額度」—— 而不是一句沒有下一步的「不行」。

決定三：插播（⚡）不受額度限制，但照樣佔掉額度
    跟輪唱對插播的處理完全一致，理由也一樣：插播是現場有人**按下去**的決定
    （生日歌、朋友要走了想唱最後一首），機器的規則不該推翻現場的人剛做的決定。

    但它照樣算進待唱數 —— 不算的話插播就變成「繞過額度」的那顆按鈕，
    而那顆按鈕在每一張歌卡上。

決定四：上限只擋新的，絕不回頭刪
    把上限調低（或本來關著、中途才打開）時，已經排進去的歌一首都不動，
    只是在降回上限以下之前點不了新的。

    另一種寫法是「超過的從後面砍掉」。那會是這個系統最嚴重的一次背叛：
    佇列是所有人盯著看的東西，有人動了一個設定，別人排好的歌就消失了 ——
    而且畫面上完全看不出是誰、為什麼。寧可規則生效得慢一點。

決定五：預設不限（0）
    跟輪唱一樣，這是一條會改變「我點不點得了歌」的規則，要包廂講好才開。
    預設開著的話，第一個被擋下來的人只會覺得點歌壞了。

決定六：擋下來的時候要講滿三件事
    誰、現在幾首、什麼時候可以再點。少講最後一件，使用者的下一個動作就是
    再按一次（然後再被擋一次）。

純資料邏輯：不碰 DOM、不發網路請求、不做 I/O。
"""
from typing import Any, Dict, List, Optional, Sequence

# 身分的定義只有一份，跟輪唱借（見決定二）。兩邊各自正規化一次的話，
# 會出現「輪序認為是同一個人、額度認為是兩個人」這種沒有人看得懂的畫面。
from backend.services.rotation import ANON_KEY, display_name, singer_key

# 0 = 不限（預設）。見決定五。
DEFAULT_PENDING_LIMIT = 0

# 上限的上限。20 首待唱已經超過一場唱得完的量，再往上調跟不限沒有差別，
# 留一個天花板只是為了讓手機端送來的怪值不會變成一個沒有意義的巨大數字。
MAX_PENDING_LIMIT = 20

# check() 的四種結論。分開講是因為畫面上要說的話完全不同：
#   off      —— 沒開額度，什麼都不必說
#   priority —— 插播，繞過額度（但照樣計數）
#   ok       —— 過了，可以順便說「還剩幾首」
#   exceeded —— 擋下來，要講滿決定六的三件事
REASON_OFF = "off"
REASON_PRIORITY = "priority"
REASON_OK = "ok"
REASON_EXCEEDED = "exceeded"


def coerce_limit(value: Any) -> int:
    """
    把設定頁／控制參數送來的上限收斂成 0..MAX_PENDING_LIMIT 的整數。

    看不懂（空字串、文字、None）一律當作 0＝不限，而不是拋錯：這個值會從
    手機端、設定檔、舊版前端三個地方送進來，其中任何一個送了怪東西時，
    正確的行為是「這條規則沒開」，不是讓點歌整個掛掉。
    """
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PENDING_LIMIT
    return max(0, min(MAX_PENDING_LIMIT, limit))


def _key_of(item: Dict[str, Any]) -> str:
    return singer_key(item.get("requested_by"))


def pending_counts(queue: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """
    每個人現在有幾首在等。

    算的是佇列，不含正在唱的那一首 —— 已經上台的歌不再佔額度，
    所以「等一首唱完就空出一格」這句承諾是真的（決定一）。
    """
    counts: Dict[str, int] = {}
    for item in queue:
        key = _key_of(item)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _next_position(queue: Sequence[Dict[str, Any]], key: str) -> Optional[int]:
    """他排最前面的那一首在佇列的第幾位（1-based）。一首都沒有回 None。"""
    for index, item in enumerate(queue):
        if _key_of(item) == key:
            return index + 1
    return None


def check(queue: Sequence[Dict[str, Any]],
          singer: Any,
          limit: Any,
          priority: bool = False) -> Dict[str, Any]:
    """
    這個人現在還點不點得了歌。

    回傳一份結論而不是 True/False：擋下來的時候畫面要講滿「誰、現在幾首、
    什麼時候可以再點」（決定六），這三件事都在這份結論裡，呼叫端不必再算一次
    （算兩次就會有兩套說法）。
    """
    limit = coerce_limit(limit)
    key = singer_key(singer)
    pending = pending_counts(queue).get(key, 0)
    verdict = {
        "limit": limit,
        "pending": pending,
        "name": display_name(singer),
        "anonymous": key == ANON_KEY,
        "next_position": _next_position(queue, key),
    }

    if limit <= 0:
        return {**verdict, "allowed": True, "reason": REASON_OFF, "remaining": None}
    if priority:
        # 決定三：現場按下去的決定不受機器的規則管，但這一首照樣會被算進
        # pending（它就在佇列裡），所以他下一首普通點歌會更早被擋下來。
        return {**verdict, "allowed": True, "reason": REASON_PRIORITY,
                "remaining": max(0, limit - pending)}
    if pending >= limit:
        return {**verdict, "allowed": False, "reason": REASON_EXCEEDED, "remaining": 0}
    return {**verdict, "allowed": True, "reason": REASON_OK,
            "remaining": limit - pending - 1}


def state(queue: Sequence[Dict[str, Any]], singer: Any, limit: Any) -> Dict[str, Any]:
    """
    這個人**現在**的額度狀況（已經排了幾首、還剩幾首）。

    刻意跟 check() 分開，因為兩者問的是不同時間點的問題，而且只差一首：
    check() 問「再加一首可不可以」（所以 remaining 扣掉正要加的那一首），
    state() 問「現在站在哪」（所以 remaining 就是上限減掉現有的）。
    共用一個函式的話，點完歌之後回報的剩餘量會固定少一首 —— 而使用者會拿它
    跟畫面上那一行「2/3」對照，對不上就不會再相信任何一邊。
    """
    limit = coerce_limit(limit)
    key = singer_key(singer)
    pending = pending_counts(queue).get(key, 0)
    return {
        "limit": limit,
        "pending": pending,
        "remaining": max(0, limit - pending) if limit > 0 else None,
        "full": limit > 0 and pending >= limit,
        "name": display_name(singer),
        "anonymous": key == ANON_KEY,
    }


def quota_summary(queue: Sequence[Dict[str, Any]], limit: Any) -> Dict[str, Any]:
    """
    給畫面用的額度全貌：現在誰排了幾首、還剩幾首。

    只列**佇列上還有歌**的人。唱完就沒歌的人不佔額度，把他列出來寫「0/3」
    只是佔掉手機上那一行的寬度（真正想知道的永遠是「誰快滿了」）。
    排序照他下一首在佇列的位置 —— 跟輪序那一行同一個順序，兩行講的是同一排歌。
    """
    limit = coerce_limit(limit)
    counts = pending_counts(queue)
    singers: List[Dict[str, Any]] = []
    seen = set()
    for item in queue:
        key = _key_of(item)
        if key in seen:
            continue
        seen.add(key)
        pending = counts.get(key, 0)
        singers.append({
            "key": key,
            "name": display_name(item.get("requested_by")),
            "anonymous": key == ANON_KEY,
            "pending": pending,
            "remaining": max(0, limit - pending) if limit > 0 else None,
            "full": limit > 0 and pending >= limit,
        })
    return {"limit": limit, "singers": singers}


class QuotaExceeded(Exception):
    """
    額度滿了，這一首不收。

    帶著整份 check() 的結論一起丟出來，API 層才講得出決定六那三件事；
    只丟一句字串的話，那句話就得在 QueueManager 裡拼 —— 那是畫面的工作，
    而且點歌台與手機端可能想用不同的說法。
    """

    def __init__(self, verdict: Dict[str, Any]):
        self.verdict = verdict
        name = verdict.get("name") or "這位"
        super().__init__(
            f"{name}已經排了 {verdict.get('pending')} 首待唱"
            f"（上限 {verdict.get('limit')}）")
