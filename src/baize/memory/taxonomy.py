"""安全知识本体（Security Taxonomy）：漏洞类型 / 场景 / 技术栈的分层知识树。

为什么需要它
------------
此前图谱里的实体是**具体资产**（domain:node5.anna.nssctf.cn、ip:1.2.3.4、
product:nginx 1.18.0），边只是「两个资产出现在同一条经验里」。这样的图只能回答
「这个域名还关联过什么」，回答不了「遇到登录框该试什么打法」「SQL 注入和命令
注入是不是同一类攻击面」——它是一张共现图，不是知识图谱。

更关键的是，把目标资产写进长期记忆**既无必要也不安全**：换一次任务资产全变，
上次记住的域名毫无用处；而客户域名、IP 留在库里本身就是敏感信息，违背渗透
测试的数据最小化原则。

记忆真正该留下来的是**类型级知识**：遇到什么场景、用什么漏洞去打。区别在于
「记住 Shiro 默认密钥可导致反序列化」是有用的知识，「记住目标 A 的 8080 端口
跑着 Shiro」不是。所以本模块把知识组织成一棵树：

- category：抽象分类（注入攻击、访问控制、信息泄露…）
- vuln     ：具体漏洞类型 / 攻击面（SQL 注入、越权、文件上传…）
- scenario ：遇到的场景（登录入口、文件上传点、API 接口…）
- component：技术栈组件（Shiro、Fastjson、Struts2…）

并预置两类语义边（见 semantic_edges）：

- IS_A      ：子节点 → 父节点（SQL 注入 IS_A 注入攻击）
- APPLIES_TO：漏洞/组件 → 场景（弱口令 APPLIES_TO 登录入口）

有了它们，检索才能做**语义扩展**而不是字面匹配：查「SQL 注入怎么打」时，除了
命中 vuln:sqli，还能沿兄弟节点（命令注入、模板注入）、父节点（注入攻击）以及
适用场景（登录框、搜索框）展开，把同类攻击面的经验一起召回——这正是「换个目标
仍是同类漏洞」时最需要的联想能力。

节点是静态预置的，不依赖任何一次具体任务是否发生过；因此即使某类漏洞还没遇到
过，查询也能命中它并沿本体找到近邻。这是它与「只记录出现过的实体」最本质的
差别。
"""
from __future__ import annotations

import re

from .models import REL_APPLIES_TO, REL_IS_A

KIND_CATEGORY = "category"
KIND_VULN = "vuln"
KIND_SCENARIO = "scenario"
KIND_COMPONENT = "component"

ROOT = "web"

# (key, kind, label, parent, aliases)；parent 为空表示根。
# 场景是独立维度，不与漏洞同树，故单独声明。
_NODES: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    (ROOT, KIND_CATEGORY, "Web 安全", "", ("web安全", "web 安全", "渗透测试", "pentest")),

    # ── 信息收集与资产发现 ──
    ("recon", KIND_CATEGORY, "信息收集与资产发现", ROOT,
     ("信息收集", "资产发现", "资产探测", "打点", "侦察")),
    ("subdomain", KIND_VULN, "子域名收集", "recon",
     ("子域名", "子域名收集", "子域名爆破", "域名收集", "泛解析")),
    ("portscan", KIND_VULN, "端口与服务探测", "recon",
     ("端口扫描", "端口探测", "服务探测", "端口爆破", "服务识别")),
    ("fingerprint", KIND_VULN, "指纹识别", "recon",
     ("指纹识别", "指纹", "cms 识别", "waf 识别", "框架识别")),

    # ── 注入攻击 ──
    ("injection", KIND_CATEGORY, "注入攻击", ROOT, ("注入", "注入攻击", "注入类")),
    ("sqli", KIND_VULN, "SQL 注入", "injection",
     ("sql 注入", "sql注入", "sqli", "sql injection", "union 注入", "报错注入",
      "盲注", "时间盲注", "布尔盲注", "堆叠注入", "二次注入", "宽字节注入")),
    ("cmdi", KIND_VULN, "命令注入与代码执行", "injection",
     ("命令注入", "命令执行", "rce", "远程代码执行", "代码执行",
      "command injection", "remote code execution", "任意命令执行")),
    ("ssti", KIND_VULN, "模板注入", "injection",
     ("模板注入", "ssti", "server side template injection", "jinja2 注入",
      "freemarker", "velocity 注入")),
    ("xxe", KIND_VULN, "XXE 外部实体", "injection",
     ("xxe", "xml 外部实体", "xml external entity")),
    ("xpath", KIND_VULN, "XPath 注入", "injection", ("xpath 注入", "xpath")),
    ("ldap-injection", KIND_VULN, "LDAP 注入", "injection",
     ("ldap 注入", "ldap injection")),

    # ── 跨站脚本 ──
    ("xss", KIND_VULN, "跨站脚本", ROOT,
     ("xss", "跨站脚本", "跨站脚本攻击", "cross site scripting")),
    ("xss-reflected", KIND_VULN, "反射型 XSS", "xss", ("反射型 xss", "反射 xss")),
    ("xss-stored", KIND_VULN, "存储型 XSS", "xss", ("存储型 xss", "持久型 xss")),
    ("xss-dom", KIND_VULN, "DOM 型 XSS", "xss", ("dom xss", "dom 型 xss")),

    # ── 文件操作 ──
    ("file-op", KIND_CATEGORY, "文件操作", ROOT, ("文件操作", "文件类漏洞")),
    ("upload", KIND_VULN, "文件上传", "file-op",
     ("文件上传", "任意文件上传", "file upload", "上传漏洞", "上传 getshell",
      "上传木马", "上传绕过", "解析漏洞")),
    ("file-include", KIND_VULN, "文件包含", "file-op",
     ("文件包含", "lfi", "rfi", "local file inclusion", "remote file inclusion")),
    ("file-read", KIND_VULN, "任意文件读取与下载", "file-op",
     ("任意文件读取", "任意文件下载", "目录穿越", "路径穿越", "path traversal",
      "任意文件读取下载", "文件读取漏洞")),

    # ── 服务端请求伪造 ──
    ("ssrf", KIND_VULN, "服务端请求伪造", ROOT,
     ("ssrf", "服务端请求伪造", "server side request forgery")),

    # ── 反序列化 ──
    ("deserialization", KIND_VULN, "反序列化", ROOT,
     ("反序列化", "deserialization", "反序列化漏洞")),
    ("deser-java", KIND_VULN, "Java 反序列化", "deserialization",
     ("java 反序列化", "java反序列化")),
    ("deser-php", KIND_VULN, "PHP 反序列化", "deserialization",
     ("php 反序列化", "php反序列化")),
    ("deser-python", KIND_VULN, "Python 反序列化", "deserialization",
     ("python 反序列化", "pickle 反序列化")),

    # ── 认证与会话 ──
    ("auth", KIND_CATEGORY, "认证与会话", ROOT, ("认证", "身份验证", "会话")),
    ("weak-credential", KIND_VULN, "弱口令与爆破", "auth",
     ("弱口令", "弱密码", "暴力破解", "爆破", "brute force", "默认口令",
      "撞库", "万能密码", "口令爆破")),
    ("auth-bypass", KIND_VULN, "认证绕过", "auth",
     ("认证绕过", "登录绕过", "auth bypass", "身份验证绕过", "鉴权绕过")),
    ("captcha-bypass", KIND_VULN, "验证码绕过", "auth",
     ("验证码绕过", "验证码爆破", "验证码复用", "验证码回显")),
    ("session-flaw", KIND_VULN, "会话与令牌安全", "auth",
     ("会话固定", "jwt 漏洞", "jwt 伪造", "令牌泄露", "session 劫持")),

    # ── 访问控制 ──
    ("access-control", KIND_CATEGORY, "访问控制", ROOT, ("访问控制", "权限")),
    ("idor", KIND_VULN, "越权访问", "access-control",
     ("越权", "idor", "水平越权", "垂直越权", "insecure direct object reference")),
    ("unauth-access", KIND_VULN, "未授权访问", "access-control",
     ("未授权访问", "匿名访问", "未认证访问", "接口未授权")),
    ("privesc", KIND_VULN, "权限提升", "access-control",
     ("提权", "权限提升", "privilege escalation", "内核提权", "sudo 提权")),

    # ── 逻辑漏洞 ──
    ("logic", KIND_CATEGORY, "逻辑漏洞", ROOT, ("逻辑漏洞", "业务逻辑")),
    ("logic-flaw", KIND_VULN, "业务逻辑漏洞", "logic",
     ("逻辑漏洞", "支付漏洞", "金额篡改", "任意用户密码修改", "订单篡改")),
    ("password-reset", KIND_VULN, "任意密码重置", "logic",
     ("任意密码重置", "密码重置漏洞", "重置密码")),
    ("race-condition", KIND_VULN, "竞争条件", "logic",
     ("竞争条件", "并发漏洞", "条件竞争", "race condition")),

    # ── 信息泄露 ──
    ("info-leak", KIND_CATEGORY, "信息泄露", ROOT,
     ("信息泄露", "信息泄漏", "敏感信息泄露")),
    ("source-leak", KIND_VULN, "源码泄露", "info-leak",
     ("源码泄露", "git 泄露", "svn 泄露", "git泄露", "代码泄露")),
    ("backup-file", KIND_VULN, "备份与配置文件", "info-leak",
     ("备份文件", "网站备份", "配置文件泄露", "数据库备份")),
    ("dir-listing", KIND_VULN, "目录列举与后台暴露", "info-leak",
     ("目录列举", "目录遍历", "目录爆破", "后台扫描", "目录扫描")),

    # ── 服务端组件与框架 ──
    # 只收「遇到它就有对应打法」的组件；纯 Web 服务器指纹（nginx/apache/iis）
    # 不收——知道目标跑 nginx 推不出任何打法，那是资产指纹不是知识。
    ("component", KIND_CATEGORY, "服务端组件与框架", ROOT, ("组件", "中间件", "框架")),
    ("shiro", KIND_COMPONENT, "Apache Shiro", "component", ("shiro", "apache shiro")),
    ("fastjson", KIND_COMPONENT, "Fastjson", "component", ("fastjson",)),
    ("struts2", KIND_COMPONENT, "Apache Struts2", "component",
     ("struts2", "struts", "s2-")),
    ("thinkphp", KIND_COMPONENT, "ThinkPHP", "component",
     ("thinkphp", "tp5", "tp6")),
    ("weblogic", KIND_COMPONENT, "Oracle WebLogic", "component", ("weblogic",)),
    ("jboss", KIND_COMPONENT, "JBoss", "component", ("jboss",)),
    ("tomcat", KIND_COMPONENT, "Apache Tomcat", "component", ("tomcat",)),
    ("log4j", KIND_COMPONENT, "Log4j2", "component",
     ("log4j", "log4j2", "log4shell")),
    ("spring", KIND_COMPONENT, "Spring", "component",
     ("spring", "spring boot", "springboot", "actuator")),
    ("wordpress", KIND_COMPONENT, "WordPress", "component", ("wordpress", "wp")),
    ("jenkins", KIND_COMPONENT, "Jenkins", "component", ("jenkins",)),
    ("redis", KIND_COMPONENT, "Redis", "component", ("redis",)),
    ("mongodb", KIND_COMPONENT, "MongoDB", "component", ("mongodb", "mongo")),
    ("mysql", KIND_COMPONENT, "MySQL", "component", ("mysql", "mariadb")),
    ("elasticsearch", KIND_COMPONENT, "Elasticsearch", "component",
     ("elasticsearch", "es")),

    # ── 客户端与浏览器安全 ──
    ("client-side", KIND_CATEGORY, "客户端与浏览器安全", ROOT,
     ("客户端", "浏览器", "前端")),
    ("csrf", KIND_VULN, "跨站请求伪造", "client-side",
     ("csrf", "跨站请求伪造", "cross site request forgery")),
    ("cors", KIND_VULN, "CORS 配置不当", "client-side",
     ("cors", "跨域配置", "cors 配置不当")),
    ("clickjacking", KIND_VULN, "点击劫持", "client-side",
     ("点击劫持", "clickjacking")),
    ("jsonp", KIND_VULN, "JSONP 劫持", "client-side", ("jsonp", "jsonp 劫持")),

    # ── 后渗透 ──
    ("post-exploit", KIND_CATEGORY, "后渗透", ROOT, ("后渗透", "内网")),
    ("getshell", KIND_VULN, "GetShell 与反弹 Shell", "post-exploit",
     ("getshell", "反弹 shell", "反弹shell", "reverse shell", "拿到 shell",
      "上线", "webshell")),
    ("lateral", KIND_VULN, "内网横向移动", "post-exploit",
     ("内网横向", "横向移动", "内网渗透", "代理隧道", "内网转发")),
    ("persistence", KIND_VULN, "权限维持", "post-exploit",
     ("权限维持", "留后门", "后门")),

    # ── 数据库安全 ──
    ("database", KIND_CATEGORY, "数据库安全", ROOT, ("数据库", "数据库安全")),
    ("db-privesc", KIND_VULN, "数据库提权", "database",
     ("udf 提权", "mof 提权", "数据库提权", "mysql 提权")),
)

# 场景：(key, label, aliases)
_SCENARIOS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("login", "登录与认证入口",
     ("登录框", "登录页面", "登录接口", "后台登录", "login", "认证入口",
      "统一认证", "登录处", "登录点")),
    ("admin", "后台管理入口",
     ("后台", "管理后台", "后台管理", "admin", "管理员", "控制台", "管理端")),
    ("upload-point", "文件上传点",
     ("上传点", "上传功能", "上传接口", "附件上传", "头像上传", "upload",
      "上传处", "文件上传点")),
    ("download-point", "文件下载与读取点",
     ("下载点", "文件下载", "下载功能", "读取文件", "附件下载", "下载处")),
    ("search-box", "搜索与查询功能",
     ("搜索框", "搜索功能", "查询接口", "搜索", "search", "检索功能")),
    ("comment", "评论与留言功能",
     ("评论", "留言", "留言板", "评论框", "feedback", "反馈", "评论功能")),
    ("api-endpoint", "API 接口",
     ("api", "接口", "restful", "graphql", "api 接口", "openapi", "swagger")),
    ("cms-site", "CMS 建站系统",
     ("cms", "建站", "dedecms", "帝国", "phpcms", "建站系统")),
    ("oa-system", "OA 与办公系统",
     ("oa", "办公系统", "协同办公", "通达", "泛微", "致远")),
    ("db-exposed", "数据库服务暴露",
     ("数据库暴露", "3306", "6379", "27017", "数据库端口", "redis 未授权",
      "mongodb 未授权")),
    ("source-repo", "源码与代码仓库",
     ("源码", "代码仓库", "git", "svn", "github", "gitlab", "代码托管")),
    ("cloud-storage", "云与对象存储",
     ("对象存储", "oss", "cos", "s3", "云存储", "bucket")),
    ("reverse-proxy", "反向代理与 CDN",
     ("反向代理", "反代", "cdn", "nginx 反代", "负载均衡", "反代入口")),
    ("iot-device", "IoT 与工控设备",
     ("iot", "工控", "摄像头", "路由器", "嵌入式", "工控设备")),
    ("mobile-api", "移动端与 App 接口",
     ("app", "移动端", "小程序", "apk", "安卓", "ios")),
    ("file-manager", "文件管理与网盘",
     ("文件管理", "文件管理器", "网盘", "网盘系统")),
)

# 场景 → 该场景下值得尝试的漏洞类型（APPLIES_TO 的知识来源）
_SCENARIO_VULNS: dict[str, tuple[str, ...]] = {
    "login": ("weak-credential", "auth-bypass", "sqli", "captcha-bypass",
              "password-reset"),
    "admin": ("weak-credential", "privesc", "upload", "unauth-access"),
    "upload-point": ("upload", "file-read", "getshell"),
    "download-point": ("file-read", "file-include"),
    "search-box": ("sqli", "xss", "ssti", "info-leak"),
    "comment": ("xss", "sqli", "csrf", "logic-flaw"),
    "api-endpoint": ("idor", "unauth-access", "ssrf", "logic-flaw", "info-leak"),
    "cms-site": ("cmdi", "sqli", "info-leak", "upload"),
    "oa-system": ("weak-credential", "upload", "file-read", "cmdi",
                  "deserialization"),
    "db-exposed": ("weak-credential", "unauth-access", "privesc", "db-privesc"),
    "source-repo": ("source-leak", "info-leak"),
    "cloud-storage": ("unauth-access", "info-leak", "upload"),
    "reverse-proxy": ("info-leak", "ssrf", "subdomain"),
    "iot-device": ("weak-credential", "cmdi", "unauth-access"),
    "mobile-api": ("unauth-access", "idor", "logic-flaw", "cmdi"),
    "file-manager": ("file-read", "upload", "unauth-access", "cmdi"),
}

# 组件 → 该组件常见的打法
_COMPONENT_VULNS: dict[str, tuple[str, ...]] = {
    "shiro": ("deserialization", "cmdi"),
    "fastjson": ("deserialization", "cmdi"),
    "struts2": ("cmdi", "ssti"),
    "thinkphp": ("cmdi", "sqli", "file-read"),
    "weblogic": ("deserialization", "cmdi", "unauth-access"),
    "jboss": ("deserialization", "unauth-access", "cmdi"),
    "tomcat": ("weak-credential", "upload", "cmdi"),
    "log4j": ("cmdi", "deserialization"),
    "spring": ("cmdi", "info-leak", "unauth-access"),
    "wordpress": ("weak-credential", "sqli", "upload", "cmdi"),
    "jenkins": ("unauth-access", "cmdi", "weak-credential"),
    "redis": ("unauth-access", "privesc", "getshell"),
    "mongodb": ("unauth-access", "info-leak"),
    "mysql": ("weak-credential", "db-privesc", "sqli"),
    "elasticsearch": ("unauth-access", "cmdi", "info-leak"),
}

# ---- 索引构建 -------------------------------------------------------------
_NODES_BY_KEY: dict[str, dict] = {}
for _k, _kind, _label, _parent, _aliases in _NODES:
    _NODES_BY_KEY[_k] = {"key": _k, "kind": _kind, "label": _label,
                         "parent": _parent, "aliases": _aliases}
for _k, _label, _aliases in _SCENARIOS:
    _NODES_BY_KEY[_k] = {"key": _k, "kind": KIND_SCENARIO, "label": _label,
                         "parent": "", "aliases": _aliases}

_CHILDREN: dict[str, list[str]] = {}
for _k, _kind, _label, _parent, _aliases in _NODES:
    if _parent:
        _CHILDREN.setdefault(_parent, []).append(_k)

# APPLIES_TO 的两端索引：vuln/component ↔ scenario
_APPLIES: dict[str, set[str]] = {}


def _link(a: str, b: str) -> None:
    _APPLIES.setdefault(a, set()).add(b)
    _APPLIES.setdefault(b, set()).add(a)


for _s, _vs in _SCENARIO_VULNS.items():
    for _v in _vs:
        if _v in _NODES_BY_KEY:
            _link(_s, _v)
for _c, _vs in _COMPONENT_VULNS.items():
    for _v in _vs:
        if _v in _NODES_BY_KEY:
            _link(_c, _v)


# ---- 基础查询 -------------------------------------------------------------
def node(key: str) -> dict | None:
    return _NODES_BY_KEY.get(key)


def entity_key(key: str) -> str:
    """本体节点 → 图上实体 key（如 vuln:sqli）。"""
    n = _NODES_BY_KEY.get(key)
    return f"{n['kind']}:{key}" if n else f"{KIND_VULN}:{key}"


def kind_of(key: str) -> str:
    n = _NODES_BY_KEY.get(key)
    return n["kind"] if n else KIND_VULN


def parent(key: str) -> str:
    n = _NODES_BY_KEY.get(key)
    return n["parent"] if n else ""


def children(key: str) -> list[str]:
    return list(_CHILDREN.get(key, []))


def siblings(key: str) -> list[str]:
    p = parent(key)
    if not p:
        return []
    return [c for c in children(p) if c != key]


def ancestors(key: str) -> list[str]:
    out, cur = [], parent(key)
    while cur and cur not in out:
        out.append(cur)
        cur = parent(cur)
    return out


def descendants(key: str) -> list[str]:
    out, stack = [], list(children(key))
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.append(cur)
        stack.extend(children(cur))
    return out


def applies(key: str) -> list[str]:
    """与该节点有 APPLIES_TO 关联的节点（漏洞 ↔ 场景、组件 → 漏洞）。"""
    return sorted(_APPLIES.get(key, set()))


def all_nodes() -> list[dict]:
    """全部本体节点，供落库使用（含 kind / label / aliases / parent）。"""
    return [dict(n) for n in _NODES_BY_KEY.values()]


def semantic_edges() -> list[tuple[str, str, str]]:
    """本体的语义边：(rel_type, source_entity_key, target_entity_key)。

    - IS_A：子 → 父，承载层级（SQL 注入 IS_A 注入攻击）；
    - APPLIES_TO：场景 → 漏洞、组件 → 漏洞，承载「什么场景用什么打法」。
    """
    edges: list[tuple[str, str, str]] = []
    for key, n in _NODES_BY_KEY.items():
        if n["parent"]:
            edges.append((REL_IS_A, entity_key(key), entity_key(n["parent"])))
    for _source, _targets in list(_SCENARIO_VULNS.items()) + list(_COMPONENT_VULNS.items()):
        for _t in _targets:
            if _t in _NODES_BY_KEY:
                edges.append((REL_APPLIES_TO, entity_key(_source), entity_key(_t)))
    return edges


# ---- 文本命中 -------------------------------------------------------------
def mentions(low: str, alias: str) -> bool:
    """别名命中判定：纯 ASCII 用词边界，中文用子串（中文无词边界）。

    放在本体层而不是图谱层，是因为本体的别名同样需要这套判定，而本体不该
    反向依赖图谱实现。
    """
    if not alias:
        return False
    if alias.isascii():
        return re.search(rf"\\b{re.escape(alias)}\\b", low) is not None
    return alias in low


def match_pairs(text: str) -> list[tuple[str, str]]:
    """命中 (节点 key, 文本中实际命中的写法)，按写法长度降序。

    返回写法而不只是节点，是因为图谱要把「本次文本里实际怎么说的」记为实体
    别名——同一漏洞的不同叫法累积起来，下次换个叫法查询也能命中，这正是
    「换个目标仍是同类漏洞」时字面匹配失效的补救。
    """
    if not text:
        return []
    low = text.lower()
    hits: list[tuple[int, str, str]] = []
    for key, n in _NODES_BY_KEY.items():
        best = ""
        for alias in n["aliases"]:
            if mentions(low, alias) and len(alias) > len(best):
                best = alias
        if best:
            hits.append((len(best), key, best))
    hits.sort(key=lambda x: x[0], reverse=True)
    return [(k, a) for _, k, a in hits]


def match(text: str) -> list[str]:
    """从文本确定性命中本体节点，返回节点 key（按别名长度降序）。

    长别名优先：``sql 注入`` 与 ``注入`` 都能命中时前者更具体，先登记它，
    避免被更泛的写法抢先占位而稀释语义。
    """
    out, seen = [], set()
    for key, _ in match_pairs(text):
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def label(key: str) -> str:
    n = _NODES_BY_KEY.get(key)
    return n["label"] if n else key


# ---- 语义扩展 -------------------------------------------------------------
# 语义距离步长：直接父子 1；兄弟与适用场景 2（同类但不同节点）
_STEP_PARENT = 1
_STEP_CHILD = 1
_STEP_SIBLING = 2
_STEP_APPLIES = 2


def expand(keys, max_depth: int = 2, max_nodes: int = 80) -> dict[str, int]:
    """从命中节点出发做本体语义扩展，返回 {节点 key: 语义距离}。

    这里的距离不是图上跳数，而是**语义远近**：自身 0，直接父子 1，兄弟与适用
    场景 2，再外扩一层在此基础上 +2。它作为图距离通道的初始种子，于是「同类
    攻击面」的经验即使字面完全不同也能被召回，且仍排在直接命中之后。

    为什么不直接把本体边丢给 BFS 去走：BFS 只认跳数不认语义，IS_A 与
    APPLIES_TO 在它眼里等价，而「SQL 注入的兄弟节点」与「SQL 注入适用的场景」
    相关度并不相同。显式给出距离，语义强弱才可控。
    """
    out: dict[str, int] = {}
    frontier: list[str] = []
    for k in keys:
        if k in _NODES_BY_KEY and k not in out:
            out[k] = 0
            frontier.append(k)
    if not frontier:
        return out

    def _neighbours(cur: str) -> list[tuple[str, int]]:
        ns: list[tuple[str, int]] = []
        p = parent(cur)
        if p:
            ns.append((p, _STEP_PARENT))
            ns.extend((s, _STEP_SIBLING) for s in siblings(cur))
        ns.extend((c, _STEP_CHILD) for c in children(cur))
        ns.extend((a, _STEP_APPLIES) for a in applies(cur))
        return ns

    for depth in range(1, max(1, max_depth) + 1):
        nxt: list[str] = []
        bump = 0 if depth == 1 else 2
        for cur in frontier:
            for nb, step in _neighbours(cur):
                if nb in out:
                    continue
                out[nb] = step + bump
                nxt.append(nb)
                if len(out) >= max_nodes:
                    return out
        frontier = nxt
        if not frontier:
            break
    return out
