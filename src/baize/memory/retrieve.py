"""Hybrid Retrieval：经验文本 + 语义事实 + 实体邻域图扩展的混合召回。

不调用 LLM。默认纯词面/实体匹配，可复现、零成本；若配置了 embedding 服务则
多一条语义通道（见 embed.py），未配置时该通道自动缺席。返回结构让 Agent /
API / UI 都能拿到按类型分组的记忆，并由上层决定注入格式。混合方式：
1. 经验层：三路召回——BM25 词面、可选向量语义、图距离（实体在图上离查询
   实体多远），经 RRF 融合后再由精排层定序（可选 cross-encoder，未配置
   时退回本地启发式）；
2. 知识层：查询文本按安全知识本体（taxonomy）命中漏洞类型 / 场景 / 技术栈，
   再与图谱里已落库实体的标签、别名做子串命中；
3. 图扩展：命中实体先沿本体做语义扩展（同类漏洞、父分类、适用场景），再在
   实例图上扩展 1 跳邻域（Semantic 邻域召回）；
4. 事实层：与命中实体 / 关键词相关的事实文本召回。
"""
from __future__ import annotations

import logging
import math
from typing import Iterable, Optional

from . import taxonomy
from .embed import cosine, embedder, experience_text, fingerprint
from .rerank import reranker
from .graph import KnowledgeGraph, extract_entities_from_text
from .store import MemoryStore
from .utils import excerpt, token_dict

logger = logging.getLogger("baize.memory.retrieve")

_STOP = {"一个", "一些", "这个", "那个", "进行", "使用", "以及", "并且",
         "如何", "什么", "怎么", "我们", "可以", "需要", "已经", "没有",
         "the", "a", "an", "and", "for", "with", "how", "what"}


def _terms(text: str) -> dict[str, int]:
    return {t: c for t, c in token_dict(text).items() if t not in _STOP}


def _overlap_score(a: dict[str, int], b: dict[str, int]) -> float:
    """轻量文本相似：查询词在目标中出现的加权占比，稀有词有增益。"""
    if not a or not b:
        return 0.0
    score = 0.0
    hit = 0
    for t, c in a.items():
        if t in b:
            score += c + b[t]
            hit += 1
    if not hit:
        return 0.0
    return score / (sum(a.values()) + len(a) * 0.3)


def _recallable_lesson(exp: dict) -> bool:
    """人工介入却最终没走通的教训：是否允许以 draft 身份参与召回。

    这类经验置信度不足（无明确结论）落在 draft，但「试过 X 不奏效」本身就是
    高价值信息，若因 draft 被永久过滤，等于人出手了却记不住。故对
    lesson + 人工介入 的 draft 开一条通道，注入时标注「未验证」由 Agent 自行判断。
    """
    return (exp.get("status") == "draft"
            and exp.get("kind") == "lesson"
            and bool(exp.get("human_assisted")))


def _bm25_stats(docs: list[dict[str, int]]) -> tuple[dict[str, float], float]:
    """统计语料的 IDF 与平均文档长度。

    这两项是 BM25 区别于「词频重叠」的关键：IDF 压制「注入」「漏洞」这类
    几乎每条经验都有的高频词，长度归一让长文档不再天然占便宜。缺了它们
    就只能算字面重叠，不能叫 BM25。
    """
    n = len(docs)
    if not n:
        return {}, 0.0
    df: dict[str, int] = {}
    total = 0
    for d in docs:
        total += sum(d.values())
        for t in d:
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log(1.0 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    return idf, total / n


def _bm25(q_terms: dict[str, int], doc_terms: dict[str, int],
          idf: dict[str, float], avg_len: float,
          k1: float = 1.5, b: float = 0.75, k3: float = 1.5) -> float:
    """单字段 BM25：词频饱和 + 文档长度归一 + 查询词频饱和。"""
    if not q_terms or not doc_terms or not idf:
        return 0.0
    dl = sum(doc_terms.values())
    score = 0.0
    for t, qtf in q_terms.items():
        f = doc_terms.get(t, 0)
        i = idf.get(t)
        if not f or i is None:
            continue
        denom = f + k1 * (1.0 - b + b * dl / max(avg_len, 1e-9))
        qtf_w = qtf * (k3 + 1.0) / (qtf + k3)
        score += i * (f * (k1 + 1.0)) / denom * qtf_w
    return score


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _dedup_experiences(exps: list[dict], limit: int,
                       threshold: float = 0.6) -> list[dict]:
    """剔除主题高度重复的经验，每类只留排序最靠前的一条。

    BM25 召回的 top-k 常常是同一套路的多条变体（比如四条都是某类注入的不同
    写法），它们抢同一份注入预算却只提供一种信息。按 token 集合的 Jaccard
    相似度去重，保证注入的每一条都带来新信息。
    """
    out: list[dict] = []
    seen: list[set] = []
    for e in exps:
        toks = set(_terms(e.get("title", "") + " " + e.get("content", "")))
        if any(_jaccard(toks, s) >= threshold for s in seen):
            continue
        seen.append(toks)
        out.append(e)
        if len(out) >= limit:
            break
    return out


def _rrf(ranked_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion：把多路排序合成一路。

    用名次而不是分数融合，是因为 BM25 与余弦的量纲完全不同（BM25 可以到几十，
    余弦只在 -1~1），直接加权求和得先做归一化，而归一化系数很难调、换批数据
    就失效。RRF 只看名次，天然免调参。
    """
    fused: dict[str, float] = {}
    for lst in ranked_lists:
        for rank, item in enumerate(lst):
            fused[item] = fused.get(item, 0.0) + 1.0 / (k + rank + 1)
    return fused


def _entity_distances(seeds: Iterable[str], relations: list[dict],
                      max_hops: int = 2, max_nodes: int = 300,
                      seed_dist: Optional[dict[str, int]] = None) -> dict[str, int]:
    """从查询命中的实体出发做 BFS，得到图上每个实体的跳数距离。

    图通道的依据：命中实体距离 0、一跳邻域 1、再外一跳 2。渗透场景里「同类
    漏洞」指的是图上离得近，而不是用词相同——字面表述可以完全不同。

    ``seed_dist`` 是本体语义扩展给出的初始距离（见 taxonomy.expand）：命中的
    漏洞类型之外，它的兄弟类型、父分类、适用场景也带着各自的距离进来，于是
    「同类攻击面」的经验即使字面不同也能被召回，且仍排在直接命中之后。

    只有距离 ≤1 的种子继续向外扩散：距离 2（兄弟 / 适用场景）已经够远，再让
    它们扩散会把无关实体成片拉进来，噪声增长快于收益。

    ``relations`` 一次性传入而不是传一个查询函数：原先每个节点调一次
    store.list_relations(key)，等于把全表扫 N 遍（N 是种子数，本体扩展后能到
    几十）。改成建一次邻接表，扫描次数就与种子数无关了。
    """
    dist: dict[str, int] = dict(seed_dist or {})
    for k in seeds:
        dist.setdefault(k, 0)
    if not dist:
        return {}
    adj: dict[str, list[str]] = {}
    for r in relations:
        s, t = r.get("source"), r.get("target")
        if not s or not t:
            continue
        adj.setdefault(s, []).append(t)
        adj.setdefault(t, []).append(s)
    frontier = [k for k, d in dist.items() if d <= 1]
    for _ in range(max(1, max_hops)):
        nxt: list[str] = []
        for key in frontier:
            for other in adj.get(key, ()):
                if other not in dist:
                    dist[other] = dist[key] + 1
                    nxt.append(other)
            if len(dist) >= max_nodes:
                break
        frontier = nxt
        if not frontier:
            break
    return dist


def _candidate_text(exp: dict) -> str:
    """精排输入：标题 + 正文，与召回时参与打分的字段保持一致。"""
    return (exp.get("title", "") + "\n" + exp.get("content", "")).strip()


def _business_weight(exp: dict) -> float:
    """业务偏好：历史评价、人工介入、重要度。

    与相关性分数解耦——相关性交给 RRF 三路融合，这里只表达「哪条更值得先想
    起来」，作为乘性因子作用在融合分上。同类实体原先也在这里加分，现已移入
    图距离通道：那是相关性而非偏好，且跳数比「是否共享实体」细得多（能区分
    直接命中与隔一跳）。
    """
    w = 1.0
    # 重要度 + 历史评价（被采纳越多越靠前，被判无用则降权）
    useful_n = min(exp.get("useful_count", 0), 20)
    hit_n = min(exp.get("hit_count", 0), 50)
    noise_n = min(exp.get("noise_count", 0), 10)
    w *= max(0.5, 1.0 + min(exp.get("importance", 0), 10) * 0.02
             + useful_n * 0.03 + hit_n * 0.005 - noise_n * 0.02)
    # 人工介入产出的经验优先浮现：人花时间纠正过的地方最值得先想起来
    if exp.get("human_assisted"):
        w *= 1.15
    # 未验证的教训让位于已验证经验，避免推测性内容抢占上下文
    if _recallable_lesson(exp):
        w *= 0.8
    return w


class MemoryRetriever:
    """混合召回器。

    search() 返回:
      experiences —— 自然语言经验（正文/标题/标签打分）
      facts       —— 与命中实体 / 关键词相关的语义事实
      entities    —— 查询命中的实体
      neighbors   —— 从命中实体沿有效边扩展邻域 {entities, relations}
      episodes    —— 任务级召回（仅 include_episodes=True 时参与）
    """

    def __init__(self, store: MemoryStore, graph: Optional[KnowledgeGraph] = None):
        self.store = store
        self.graph = graph or KnowledgeGraph(store)

    # ---- 主入口 -------------------------------------------------------------
    def search(self, query: str, k_experiences: int = 5, k_facts: int = 5,
               k_episodes: int = 3, include_episodes: bool = False) -> dict:
        q_terms = _terms(query)
        if not q_terms:
            return {"query": query, "experiences": [], "facts": [],
                    "entities": [], "neighbors": {"entities": [], "relations": []},
                    "episodes": []}
        entities = self._match_entities(query)
        related = self._expand(entities)
        entity_keys = [e["key"] for e in entities] + [e["key"] for e in related["entities"]]
        direct_keys = [e["key"] for e in entities]
        neighbor_keys = [e["key"] for e in related["entities"]]
        # 语义通道只在配置了 embedding 服务时才存在；拿不到查询向量（未配置、
        # 服务不可用、熔断中）就自动退回纯 BM25，见 embed.py。
        qvec = embedder().embed_one_sync(query)
        experiences = self._rank_experiences(q_terms, k_experiences,
                                             direct_keys, qvec=qvec,
                                             query=query)
        facts = self._rank_facts(q_terms, entity_keys, k_facts)
        episodes = (self._rank_episodes(q_terms, k_episodes,
                                        direct_keys + neighbor_keys)
                    if include_episodes else [])
        return {
            "query": query,
            "experiences": experiences,
            "facts": facts,
            "entities": entities,
            "neighbors": related,
            "episodes": episodes,
        }

    # ---- 实体命中 ------------------------------------------------------------
    def _match_entities(self, query: str) -> list[dict]:
        """查询 → 图上实体。

        命中来源是本体（漏洞类型 / 场景 / 技术栈组件，按别名匹配）与 CVE 编号。
        本体节点是**预置**的，所以即使这类漏洞一次都没遇到过也能命中——「SQL
        注入怎么打」不会因为库里没有历史而落空，这是它与此前「只匹配已出现过
        的实体」最实际的差别。
        """
        low = query.lower()
        matched: dict[str, dict] = {}
        for key, ent in extract_entities_from_text(query).items():
            # 库里已有就用库里的：本体落库时写入的 label / 别名比临时构造的完整
            matched[key] = self.store.get_entity(key) or ent
        for ent in self.store.list_entities(limit=3000):
            if ent["key"] in matched:
                continue
            hay = [ent.get("label", ""), ent.get("key", "")] + list(ent.get("aliases", []))
            if any(h and len(h) >= 2 and h.lower() in low for h in hay):
                matched[ent["key"]] = ent
        return list(matched.values())

    # ---- 图邻域扩展 ------------------------------------------------------------
    def _expand(self, entities: list[dict]) -> dict:
        keys = [e["key"] for e in entities]
        if not keys:
            return {"entities": [], "relations": []}
        return self.graph.expand(keys, hop=1)

    @staticmethod
    def _taxonomy_seeds(direct: Iterable[str]) -> dict[str, int]:
        """把命中实体沿本体扩展成带语义距离的种子集。

        只扩一层（父/子 + 兄弟 + 适用场景）。再往外走一步几乎会覆盖整棵树：实测
        查「SQL 注入」时第二层会带进 Shiro、Struts2、Redis 等五十多个节点，图距离
        从此失去区分度——图通道变成把全库经验都拉成「隔两跳」，等于没有通道。
        一层已经够用：兄弟类型与适用场景正是「换个说法」时最该联想的部分。
        """
        bare = [k.split(":", 1)[1] for k in direct if ":" in k]
        seeds: dict[str, int] = {}
        for nkey, d in taxonomy.expand(bare, max_depth=1).items():
            seeds[taxonomy.entity_key(nkey)] = d
        return seeds

    # ---- 经验打分 ---------------------------------------------------------------
    def _rank_experiences(self, q_terms: dict[str, int], k: int,
                          entity_keys: Optional[list[str]] = None,
                          qvec: Optional[list[float]] = None,
                          query: str = "") -> list[dict]:
        """经验打分：三路召回 + RRF 融合 + 业务偏好 + 精排。

        三路各有所长也各有盲区：BM25 认词不认意，向量认意但容易把语义相近而
        目标不同的经验拉进来，图距离认实体关联却完全不看文本。融合用 RRF 而
        非加权求和，是因为三者量纲不同（BM25 可到几十、余弦在 -1~1、图距离是
        整数跳数），按名次融合免去做归一化——归一化系数换批数据就失效。

        ``entity_keys`` 是查询直接命中的实体（如某个漏洞类型 / 场景 / 技术栈）。
        邻域不再由调用方传入：图距离通道先沿知识本体把这些实体扩展成带语义距离
        的种子（同类漏洞、父分类、适用场景），再从种子出发在实例图上做 1 跳 BFS。
        两段合起来才完整——本体补「本该有关联但还没遇到过」的，实例图补「确实
        一起出现过」的。

        ``qvec`` 是查询文本的向量；为 None（未配置 embedding、服务不可用或
        熔断中）时语义通道整体缺席，结果与接入向量之前完全一致。
        """
        direct = set(entity_keys or [])
        cands: list[dict] = []
        for exp in self.store.list_experiences():
            if exp.get("status") != "active" and not _recallable_lesson(exp):
                continue
            cands.append(exp)
        if not cands:
            return []

        # ---- 通道一：BM25 字面相关 ----
        # 先扫一遍语料拿到 IDF 与平均长度，再逐条打分（两趟）。
        fields: list[tuple[dict, dict, dict]] = []
        merged_docs: list[dict[str, int]] = []
        for exp in cands:
            title = _terms(exp.get("title", ""))
            content = _terms(exp.get("content", ""))
            tags = _terms(" ".join(exp.get("tags", [])))
            fields.append((title, content, tags))
            merged: dict[str, int] = {}
            for d in (title, content, tags):
                for t, c in d.items():
                    merged[t] = merged.get(t, 0) + c
            merged_docs.append(merged)
        idf, avg_len = _bm25_stats(merged_docs)

        bm25: list[tuple[float, str]] = []
        for exp, (title, content, tags) in zip(cands, fields):
            s = (_bm25(q_terms, content, idf, avg_len) * 0.7
                 + _bm25(q_terms, title, idf, avg_len) * 0.8
                 + _bm25(q_terms, tags, idf, avg_len) * 1.0)
            if s > 0:
                bm25.append((s, exp.get("id", "")))
        bm25.sort(key=lambda x: x[0], reverse=True)

        # ---- 通道二：向量语义相关（未配置 embedding 时缺席）----
        vec_ranked: list[str] = []
        if qvec:
            sims: list[tuple[float, str]] = []
            for exp in cands:
                emb = exp.get("embedding") or {}
                # 指纹对不上说明内容改过而向量没重算，宁可不用也不能用错的
                if emb.get("fp") != fingerprint(experience_text(exp)):
                    continue
                v = emb.get("v")
                if not v or len(v) != len(qvec):
                    continue
                c = cosine(qvec, v)
                if c > 0:
                    sims.append((c, exp.get("id", "")))
            sims.sort(key=lambda x: x[0], reverse=True)
            vec_ranked = [i for _, i in sims]

        # ---- 通道三：图距离 + 本体语义扩展 ----
        # 光在实例图上 BFS 不够：命中「SQL 注入」时，若此前从未遇到过「命令注入」，
        # 图上就没有边把它们连起来，同类攻击面照样召回不到。所以先用本体把命中
        # 节点扩展成带距离的种子（兄弟类型、父分类、适用场景），再交给 BFS 走
        # 实例边——本体补的正是「本该有关联但还没遇到过」的那部分。
        # BFS 仍只走 1 跳：本体边落库后图会更密，多跳会让距离迅速失去区分度。
        dist = _entity_distances(direct, self.store.list_relations(), max_hops=1,
                                 seed_dist=self._taxonomy_seeds(direct))
        graph_ranked: list[str] = []
        if dist:
            prox: list[tuple[int, str]] = []
            for exp in cands:
                keys = set(exp.get("entity_keys") or [])
                d = min((dist[k_] for k_ in keys if k_ in dist), default=None)
                if d is not None:
                    prox.append((d, exp.get("id", "")))
            prox.sort(key=lambda x: x[0])
            graph_ranked = [i for _, i in prox]

        # ---- 融合 + 业务偏好 ----
        # 相关性走 RRF 三路，偏好（反馈 / 人工 / 重要度）走乘性因子，两者分开。
        channels = [[i for _, i in bm25]]
        if vec_ranked:
            channels.append(vec_ranked)
        if graph_ranked:
            channels.append(graph_ranked)
        if len(channels) > 1:
            fused = _rrf(channels)
        else:
            # 只有一路时保留原始分：RRF 会把分数差异压成名次差，白白丢掉 BM25
            # 的相关度强弱信息，也会让未启用语义检索时的排序发生变化
            fused = {i: s for s, i in bm25}
        by_id = {exp.get("id", ""): exp for exp in cands}
        scored: list[tuple[float, dict]] = []
        for eid, base in fused.items():
            exp = by_id.get(eid)
            if exp is None:
                continue
            scored.append((base * _business_weight(exp), exp))
        scored.sort(key=lambda x: x[0], reverse=True)

        # ---- 精排 ----
        ranked = self._rerank(query or " ".join(q_terms), scored, dist, k)
        out = []
        for exp in _dedup_experiences(ranked, limit=k):
            item = dict(exp)
            item["content"] = excerpt(item.get("content", ""), 600)
            item["evidence"] = item.get("evidence", [])[:3]
            out.append(item)
        return out

    # ---- 精排 -------------------------------------------------------------------
    def _rerank(self, query: str, scored: list[tuple[float, dict]],
                dist: dict[str, int], k: int) -> list[dict]:
        """对召回结果的头部做精排，返回仍按最终顺序排列的经验列表。

        只对前 ``max(k*3, 12)`` 条生效：cross-encoder 要对每个候选跑一次模型，
        代价与候选数成正比，而排在后面的候选本来也进不了注入预算。精排区之外
        的候选保持召回顺序原样接在后面，保证去重时仍有足够的候选池。
        """
        if not scored:
            return []
        depth = max(k * 3, 12)
        pool, rest = scored[:depth], scored[depth:]
        scores = reranker().rerank_sync(query, [_candidate_text(e) for _, e in pool])
        if scores and len(scores) == len(pool):
            head = self._blend_rerank(pool, scores)
        else:
            head = self._heuristic_rerank(query, pool, dist)
        return head + [e for _, e in rest]

    @staticmethod
    def _blend_rerank(pool: list[tuple[float, dict]], scores: list[float],
                      rerank_weight: float = 0.7) -> list[dict]:
        """精排分与召回分加权融合，而不是让精排分直接定序。

        直接按精排分定序会丢掉召回分里两类 cross-encoder 看不到的信息：

        - 业务偏好（反馈次数、人工介入、重要度）——模型只读查询与候选文本，
          不知道这条经验历史上帮没帮上忙、是不是人纠正过的；
        - 三路融合的强度差——RRF 名次背后的相关度强弱。

        所以两者按 7:3 融合：精排主导（它确实更准），偏好仍握有话语权。各自
        先在池内 min-max 归一到 [0,1] 再加权，是因为精排分是模型的 logits、
        召回分是 RRF 值，量纲不同，不归一就没法加权。分差为零时（比如模型对
        所有候选给了同一个分）整体退化为 0.5，此时排序完全由召回分决定。
        """

        def _norm(vals: list[float]) -> list[float]:
            lo, hi = min(vals), max(vals)
            if hi - lo < 1e-9:
                return [0.5] * len(vals)
            return [(v - lo) / (hi - lo) for v in vals]

        rs = _norm([float(s) for s in scores])
        bs = _norm([float(b) for b, _ in pool])
        out: list[tuple[float, dict]] = []
        for i, (_, exp) in enumerate(pool):
            out.append((rerank_weight * rs[i] + (1.0 - rerank_weight) * bs[i], exp))
        out.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in out]

    def _heuristic_rerank(self, query: str, pool: list[tuple[float, dict]],
                          dist: dict[str, int]) -> list[dict]:
        """降级精排：召回分主导，本地信号做乘性微调。

        补的是 RRF 压掉的两个强弱信号：

        - 标题覆盖度：RRF 只认名次，融合后第一名与第二名的差距是固定的，分不
          出「查询词被标题完整命中」和「只蹭到一个词」，而这两者在渗透场景里
          差别很大；
        - 图距离近度：图距离在召回阶段也参与了，但同样只按名次，这里按跳数做
          连续加权，直接命中与隔两跳不再被同等看待。

        两者都做成小幅乘性因子而不是重排依据——未配 cross-encoder 时，排序的
        主导权仍应留在召回分手里。
        """
        qset = set(_terms(query))
        out: list[tuple[float, dict]] = []
        for base, exp in pool:
            title = set(_terms(exp.get("title", "")))
            cover = len(qset & title) / max(len(qset), 1)
            keys = set(exp.get("entity_keys") or [])
            d = min((dist[k_] for k_ in keys if k_ in dist), default=None)
            prox = 1.0 / (1.0 + d) if d is not None else 0.0
            out.append((base * (1.0 + 0.25 * cover + 0.15 * prox), exp))
        out.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in out]

    # ---- 事实打分 ---------------------------------------------------------------
    def _rank_facts(self, q_terms: dict[str, int], entity_keys: list[str],
                    k: int) -> list[dict]:
        """事实打分：BM25 + 实体命中保底。

        与经验层用同一套 BM25——事实同样是文本，先前这里用的是旧的重叠打分，
        缺 IDF 与长度归一，长句事实会天然占便宜。
        """
        keyset = set(entity_keys)
        # 只召回 active：draft 尚未确认，superseded 已被更新的事实取代，两者
        # 注入上下文都是在给 Agent 喂不确定或已过时的信息。
        cands = [f for f in self.store.list_facts() if f.get("status") == "active"]
        if not cands:
            return []
        docs = [_terms(f.get("statement", "")) for f in cands]
        idf, avg_len = _bm25_stats(docs)
        scored: list[tuple[float, dict]] = []
        for fact, d in zip(cands, docs):
            s = _bm25(q_terms, d, idf, avg_len)
            # 实体命中是结构化证据，比词面匹配更硬，给一个保底分
            if fact.get("subject") in keyset or fact.get("object") in keyset:
                s = max(s, 0.5)
            if s > 0:
                scored.append((s, fact))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [dict(f) for _, f in scored[:k]]

    # ---- Episode 召回（回忆用） -----------------------------------------------------
    def _rank_episodes(self, q_terms: dict[str, int], k: int,
                       entity_keys: Optional[list[str]] = None) -> list[dict]:
        keyset = set(entity_keys or [])
        scored: list[tuple[float, dict]] = []
        for ep in self.store.list_episodes(limit=200):
            if ep.get("status") == "discarded":
                continue
            text = _terms((ep.get("task", "") + " " + ep.get("summary", "") +
                           " " + ep.get("result_text", "")))
            score = _overlap_score(q_terms, text)
            ep_keys = set(ep.get("entity_keys") or [])
            if keyset and ep_keys:
                shared = keyset & ep_keys
                if shared:
                    score += 0.25 + 0.10 * (len(shared) - 1)
            # 含人工介入的轨迹优先：那是人花过时间纠正的地方，信息密度最高
            if any(s.get("type") == "user_hint" for s in ep.get("steps", [])):
                score += 0.15
            if score > 0:
                ep = dict(ep)
                ep["steps"] = ep.get("steps", [])[:6]
                scored.append((score, ep))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in scored[:k]]

    # ---- 注入格式 ---------------------------------------------------------------
    def build_injection(self, query: str, budget_chars: int = 3200,
                        include_episodes: bool = False,
                        data: Optional[dict] = None) -> str:
        """把混合检索结果组装成注入 Agent 上下文的段落（保守、不喧宾夺主）。

        经验始终以自然语言文本呈现，Agent 自行判断是否参考。
        data 可传入已完成的 search 结果，避免同一轮重复检索。
        """
        if data is None:
            data = self.search(query, k_experiences=5, k_facts=4,
                               include_episodes=include_episodes)
        parts: list[str] = []
        budget = budget_chars
        header = ("以下是长期记忆库中可能与当前任务相关的既往知识与经验"
                  "（仅作参考，请结合实时工具结果判断其是否适用）：")
        body: list[str] = []

        exps = data["experiences"]
        if exps:
            lines: list[str] = []
            for e in exps:
                # 标注来源：人工介入产出的经验带有实测纠正背景，Agent 应优先参考
                mark = "｜人工介入" if e.get("human_assisted") else ""
                if e.get("status") != "active":
                    mark += "｜未验证"
                lines.append(f"[经验] {e.get('title','')}｜{e.get('kind','')}{mark}")
                lines.append(f"      {e.get('content','')}")
            body.append("\n".join(lines)[:int(budget * 0.6)])
            budget -= int(budget * 0.6)

        facts = data["facts"]
        if facts and budget > 400:
            lines = [f"[事实] {f.get('statement','')}（{f.get('confidence','')}）"
                     for f in facts]
            body.append("\n".join(lines)[:int(budget * 0.6)])
            budget -= int(budget * 0.6)

        ents = data["entities"] + data["neighbors"]["entities"]
        if ents and budget > 200:
            uniq = list({e["key"]: e for e in ents}.values())[:10]
            labels = [f"{e.get('label','')}({e.get('kind','')})" for e in uniq]
            body.append("知识库中已记录的关联实体：" + "、".join(labels))

        if body:
            parts.append(header)
            parts.extend(body)
        return "\n".join(parts)
