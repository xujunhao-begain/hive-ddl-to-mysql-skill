#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Hive / MaxCompute 的 CREATE TABLE 语句转成 MySQL 建表语句，可选直接在 MySQL 上执行。

默认只生成语句（打到 stdout + 落盘），不动数据库。加 --execute 才真的连库建表。
"""

import argparse
import csv
import datetime as _dt
import io
import os
import re
import sys

# ---------------------------------------------------------------- 配置读取

CONFIG_ENV = "MYSQL_CONFIG"
CONFIG_NAMES = ("config.yml", "config.yaml")


def _skill_dir():
    # realpath 而非 abspath：本技能可能通过 ~/.claude/skills/ 下的 symlink 被调用，
    # abspath 不解析软链，会让 _project_root() 上溯到错误的目录。
    return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def _project_root():
    """仓库根（技能目录本身；独立仓库后 scripts/ 的上一级即仓库根）。"""
    return _skill_dir()


def config_candidates(explicit):
    """按优先级产出候选 config 路径：--config > 环境变量 > 技能目录 > 项目根 > ~/.mysql/。"""
    if explicit:
        yield explicit
        return
    env = os.environ.get(CONFIG_ENV)
    if env:
        yield env
    for base in (_skill_dir(), _project_root(), os.path.expanduser("~/.mysql")):
        for name in CONFIG_NAMES:
            yield os.path.join(base, name)


def load_config(explicit=None):
    """读 config.yml 的 mysql: 段。找不到文件不算错——只生成语句时并不需要连接信息。"""
    try:
        import yaml
    except ImportError:
        return {}, None
    for path in config_candidates(explicit):
        if not path or not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            continue
        section = data.get("mysql") or data.get("MySQL") or {}
        if not isinstance(section, dict):
            section = {}
        return section, path
    if explicit:
        raise SystemExit("配置文件不存在：%s" % explicit)
    return {}, None


def resolve_target_db(args):
    """决定 MySQL 目标库名。

    Hive 库名（htba 等）和 MySQL 库名是两套命名，源库名照抄过来基本都是错的，
    所以【不】用 Hive DDL 里解析出的库名做默认值，而是走 config.yml 的 mysql.database。
    优先级：--db > MYSQL_DATABASE 环境变量 > config.yml 的 database。
    三者都没有时返回 None，生成裸表名（由执行时的 USE 决定落到哪个库）。
    """
    if args.db is not None:
        return args.db
    if os.environ.get("MYSQL_DATABASE"):
        return os.environ["MYSQL_DATABASE"]
    cfg, _ = load_config(args.config)
    val = cfg.get("database")
    return val if val not in (None, "") else None


def resolve_conn(args):
    """连接参数优先级：CLI > 环境变量 > config.yml。"""
    cfg, cfg_path = load_config(args.config)
    env = os.environ

    def pick(cli, env_key, cfg_key, default=None):
        if cli not in (None, ""):
            return cli
        if env.get(env_key):
            return env[env_key]
        if cfg.get(cfg_key) not in (None, ""):
            return cfg[cfg_key]
        return default

    conn = {
        "host": pick(args.host, "MYSQL_HOST", "host"),
        "port": pick(args.port, "MYSQL_PORT", "port", 3306),
        "user": pick(args.user, "MYSQL_USER", "username") or pick(None, "MYSQL_USER", "user"),
        "password": pick(args.password, "MYSQL_PASSWORD", "password"),
        "database": pick(args.database, "MYSQL_DATABASE", "database"),
        "charset": pick(None, "MYSQL_CHARSET", "charset", "utf8mb4"),
    }
    try:
        conn["port"] = int(conn["port"])
    except (TypeError, ValueError):
        raise SystemExit("port 不是合法整数：%r" % (conn["port"],))
    return conn, cfg_path


# ---------------------------------------------------------------- Hive DDL 解析

# 注释里可能出现任何东西（包括括号和逗号），所以先把它们摘出来再做括号匹配。
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _strip_sql_comments(sql):
    """去掉 SQL 注释，但保留字符串字面量里的 -- 和 /*。"""
    out = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`"):
            # 原样拷贝整个字面量/标识符
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                if sql[i] == "\\" and quote != "`" and i + 1 < n:
                    out.append(sql[i:i + 2])
                    i += 2
                    continue
                out.append(sql[i])
                if sql[i] == quote:
                    # 处理 '' 转义
                    if i + 1 < n and sql[i + 1] == quote:
                        out.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _find_matching_paren(text, start):
    """start 指向 '('，返回配对 ')' 的下标。跳过字符串和反引号。"""
    depth = 0
    i, n = start, len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\" and quote != "`" and i + 1 < n:
                    i += 2
                    continue
                if text[i] == quote:
                    if i + 1 < n and text[i + 1] == quote:
                        i += 2
                        continue
                    break
                i += 1
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("括号不配对：找不到与第 %d 位 '(' 匹配的 ')'" % start)


def _split_top_level(text, sep=","):
    """按顶层分隔符切分，忽略括号内和字符串内的分隔符。

    除了 ()，还必须跟踪 <>——Hive 复杂类型 `map<string,string>` / `struct<a:int,b:string>`
    的逗号在尖括号里，按顶层逗号切会把一列劈成两半。只在紧跟类型关键字后的 '<' 才计深度，
    避免把比较运算符 `a < b` 误当泛型开始。
    """
    parts, buf = [], []
    depth = 0
    angle = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            while i < n:
                if text[i] == "\\" and quote != "`" and i + 1 < n:
                    buf.append(text[i:i + 2])
                    i += 2
                    continue
                buf.append(text[i])
                if text[i] == quote:
                    if i + 1 < n and text[i + 1] == quote:
                        buf.append(text[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "<":
            # 只有 array/map/struct/uniontype 后面的 '<' 算泛型
            before = re.search(r"(array|map|struct|uniontype)\s*$", "".join(buf), re.I)
            if before or angle > 0:
                angle += 1
        elif ch == ">" and angle > 0:
            angle -= 1
        if ch == sep and depth == 0 and angle == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if "".join(buf).strip():
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _unquote_ident(name):
    name = name.strip()
    for q in ("`", '"'):
        if len(name) >= 2 and name.startswith(q) and name.endswith(q):
            return name[1:-1].replace(q * 2, q)
    return name


def _unquote_literal(text):
    """把 'xxx' 或 "xxx" 还原成裸字符串，处理 '' 和 \\' 转义。"""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        q = text[0]
        body = text[1:-1]
        body = body.replace(q * 2, q)
        body = body.replace("\\" + q, q).replace("\\\\", "\\")
        return body
    return text


_COL_COMMENT = re.compile(r"\bCOMMENT\s+('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")", re.I | re.S)


def parse_column(spec):
    """解析一条列定义 -> (name, hive_type, comment) 。识别不了的返回 None。"""
    spec = spec.strip().rstrip(",").strip()
    if not spec:
        return None
    # 约束子句不是列
    if re.match(r"(?i)^(primary\s+key|unique|key|index|constraint|foreign\s+key|check)\b", spec):
        return None

    comment = ""
    m = _COL_COMMENT.search(spec)
    if m:
        comment = _unquote_literal(m.group(1))
        spec = (spec[:m.start()] + " " + spec[m.end():]).strip()

    # 去掉列级修饰
    spec = re.sub(r"(?i)\bNOT\s+NULL\b", " ", spec)
    spec = re.sub(r"(?i)\bNULL\b", " ", spec)
    spec = re.sub(r"(?i)\bDEFAULT\s+\S+", " ", spec)
    spec = spec.strip()

    m = re.match(r"^(`[^`]+`|\"[^\"]+\"|[A-Za-z_][\w$]*)\s*(.*)$", spec, re.S)
    if not m:
        return None
    name = _unquote_ident(m.group(1))
    hive_type = " ".join(m.group(2).split()).strip().rstrip(",").strip()
    if not hive_type:
        return None
    return name, hive_type, comment


_CREATE_RE = re.compile(
    r"(?is)\bCREATE\s+(?:EXTERNAL\s+|TEMPORARY\s+|TRANSACTIONAL\s+)*TABLE\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?"
    r"((?:`[^`]+`|\"[^\"]+\"|[\w$]+)(?:\s*\.\s*(?:`[^`]+`|\"[^\"]+\"|[\w$]+))?)"
    r"\s*\("
)

_TBL_COMMENT = re.compile(
    r"(?is)\)\s*(?:.*?)\bCOMMENT\s+('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")")

_PARTITIONED_BY = re.compile(r"(?is)\bPARTITIONED\s+BY\s*\(")


def parse_hive_ddl(sql):
    """解析 Hive CREATE TABLE -> dict(db, table, columns, partitions, table_comment)。"""
    clean = _strip_sql_comments(sql)
    m = _CREATE_RE.search(clean)
    if not m:
        raise SystemExit(
            "没能在输入里找到 CREATE TABLE 语句。请确认传进来的是 Hive/ODPS 建表语句，"
            "而不是查询或表结构描述（describe 的输出请改用 --format describe，见 --help）。")

    raw_name = m.group(1)
    parts = [_unquote_ident(p) for p in re.split(r"\s*\.\s*", raw_name)]
    db, table = (parts[0], parts[1]) if len(parts) == 2 else (None, parts[0])

    lparen = clean.index("(", m.end() - 1)
    rparen = _find_matching_paren(clean, lparen)
    body = clean[lparen + 1:rparen]

    columns = []
    unparsed = []
    for spec in _split_top_level(body):
        parsed = parse_column(spec)
        if parsed:
            columns.append(parsed)
        else:
            unparsed.append(spec)

    tail = clean[rparen:]

    partitions = []
    pm = _PARTITIONED_BY.search(tail)
    if pm:
        p_l = tail.index("(", pm.end() - 1)
        p_r = _find_matching_paren(tail, p_l)
        for spec in _split_top_level(tail[p_l + 1:p_r]):
            parsed = parse_column(spec)
            if parsed:
                partitions.append(parsed)
        # 表级 COMMENT 只在 PARTITIONED BY 之前找，避免把分区列注释当表注释
        tail_for_comment = tail[:pm.start()]
    else:
        tail_for_comment = tail

    table_comment = ""
    tc = _TBL_COMMENT.search(tail_for_comment)
    if tc:
        table_comment = _unquote_literal(tc.group(1))

    if not columns:
        raise SystemExit("解析到 CREATE TABLE 但列清单为空，请检查语句是否完整。")

    return {
        "db": db,
        "table": table,
        "columns": columns,
        "partitions": partitions,
        "table_comment": table_comment,
        "unparsed": unparsed,
    }


def parse_describe(text):
    """兜底解析 `describe <table>` 风格的三列文本：字段名 [类型] [注释]。"""
    columns = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        line = line.strip("|").strip()
        if set(line) <= set("-+ "):
            continue
        cells = [c.strip() for c in re.split(r"\t+|\s*\|\s*|\s{2,}", line) if c.strip()]
        if not cells:
            continue
        if len(cells) == 1:
            cells = [c for c in cells[0].split() if c]
        name = _unquote_ident(cells[0])
        if not re.match(r"^[A-Za-z_][\w$]*$", name):
            continue
        if name.lower() in ("col_name", "column_name", "字段", "字段名"):
            continue
        hive_type = cells[1] if len(cells) > 1 else "string"
        comment = cells[2] if len(cells) > 2 else ""
        columns.append((name, hive_type, comment))
    if not columns:
        raise SystemExit("按 describe 格式没解析出任何字段，请检查输入。")
    return {
        "db": None, "table": None, "columns": columns,
        "partitions": [], "table_comment": "", "unparsed": [],
    }


# ---------------------------------------------------------------- 类型映射

# 直接映射：Hive/ODPS 基础类型 -> MySQL 类型
_SIMPLE_MAP = {
    "tinyint": "TINYINT",
    "smallint": "SMALLINT",
    "int": "INT",
    "integer": "INT",
    "bigint": "BIGINT",
    "long": "BIGINT",
    "float": "FLOAT",
    "double": "DOUBLE",
    "double precision": "DOUBLE",
    "real": "DOUBLE",
    "boolean": "TINYINT(1)",
    "bool": "TINYINT(1)",
    "date": "DATE",
    "datetime": "DATETIME",
    "timestamp": "DATETIME",
    "binary": "BLOB",
    "json": "JSON",
    "uniqueidentifier": "VARCHAR(64)",
}

# 长文本字段名信号：这些语义天然可能超长，给 TEXT 而不是 VARCHAR
_TEXT_HINTS = (
    "remark", "remarks", "comment", "comments", "desc", "description", "detail", "details",
    "content", "json", "ext", "extra", "extend", "extends", "params", "param", "payload",
    "body", "message", "msg", "reason", "note", "notes", "memo", "address", "url", "link",
    "img", "image", "picture", "photo", "avatar", "text", "raw", "config", "snapshot",
    "response", "request", "log", "tags", "labels", "path", "title", "summary",
)

# 短标识字段名信号 -> 更紧凑的 varchar 长度。只与字段名最后一个 token 精确比对。
_SHORT_HINTS = {
    64: ("id", "no", "code", "key", "uid", "uuid", "openid", "unionid", "sn",
         "mobile", "phone", "tel", "ip", "date", "dt", "time", "month", "week", "day",
         "type", "status", "state", "flag", "version", "ver", "year", "quarter", "hour"),
    128: ("name", "nickname", "channel", "source", "term", "city", "province", "grade",
          "school", "class", "tag", "group", "email", "brand", "dept", "role"),
}


def _norm_name_tokens(name):
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]


def map_string_type(col_name, default_len, string_type_override):
    """决定一个 Hive string 字段在 MySQL 里的类型。

    启发式仅看字段名：语义上可能很长的给 TEXT，标识/枚举类给短 VARCHAR，其余给默认长度。
    返回 (mysql_type, note)；note 非空表示这是启发式判断，需要人工复核。
    """
    if string_type_override:
        return string_type_override.upper(), ""

    tokens = _norm_name_tokens(col_name)
    if not tokens:
        return "VARCHAR(%d)" % default_len, "无字段名信号，给默认 VARCHAR(%d)" % default_len

    # 只看**最后一个** token（snake_case 命名里的语义中心词）。
    # 匹配任意位置的 token 会误判：no_comment_col 的 comment 是修饰语而非中心词，
    # 它不该因此变成 TEXT。
    head = tokens[-1]

    if head in _TEXT_HINTS:
        return "TEXT", "字段名以「%s」结尾，按长文本给 TEXT" % head

    for length, hints in sorted(_SHORT_HINTS.items()):
        for hint in hints:
            if head == hint.strip("_"):
                return "VARCHAR(%d)" % length, "按短标识字段（%s）给 VARCHAR(%d)" % (head, length)

    return "VARCHAR(%d)" % default_len, "无字段名信号，给默认 VARCHAR(%d)" % default_len


def map_type(col_name, hive_type, args):
    """Hive 类型 -> (mysql_type, note, warning)。"""
    t = " ".join((hive_type or "").split()).strip().rstrip(",")
    low = t.lower()

    if not low:
        mt, note = map_string_type(col_name, args.varchar_len, args.string_type)
        return mt, (note or "原始类型缺失，按 string 处理"), "原始类型缺失，按 string 处理"

    # 复杂类型：MySQL 没有对应物，落到 JSON/TEXT 并明确告警
    if re.match(r"^(array|map|struct|union)\s*<", low) or low in ("array", "map", "struct"):
        target = "JSON" if args.complex_type.lower() == "json" else args.complex_type.upper()
        return target, "Hive 复杂类型 %s 无对应物，落成 %s" % (t, target), \
            "复杂类型 %s -> %s，需确认写入端是否按序列化后的字符串写" % (t, target)

    # decimal / numeric（带精度）
    m = re.match(r"^(decimal|numeric)\s*(\(\s*\d+\s*(?:,\s*\d+\s*)?\))?$", low)
    if m:
        spec = m.group(2)
        if spec:
            return "DECIMAL" + re.sub(r"\s+", "", spec), "", ""
        return "DECIMAL(%s)" % args.decimal_default, \
            "Hive decimal 未带精度，按 --decimal-default 给 DECIMAL(%s)" % args.decimal_default, ""

    # char / varchar（带长度）
    m = re.match(r"^(var)?char\s*\(\s*(\d+)\s*\)$", low)
    if m:
        length = int(m.group(2))
        if length > args.varchar_max:
            return "TEXT", "原长度 %d 超过 --varchar-max(%d)，改用 TEXT" % (length, args.varchar_max), ""
        kind = "VARCHAR" if m.group(1) else "CHAR"
        return "%s(%d)" % (kind, length), "", ""
    if low in ("char", "varchar"):
        mt, note = map_string_type(col_name, args.varchar_len, args.string_type)
        return mt, note, ""

    if low in ("string", "text"):
        mt, note = map_string_type(col_name, args.varchar_len, args.string_type)
        return mt, note, ""

    if low in _SIMPLE_MAP:
        return _SIMPLE_MAP[low], "", ""

    # 认不出：原样大写透传，让 MySQL 自己去报错，但先在报告里标出来
    return t.upper(), "无法识别的类型，原样透传", "无法识别的 Hive 类型 %r，原样透传，请人工确认" % t


# ---------------------------------------------------------------- MySQL DDL 生成

_MYSQL_RESERVED = {
    "add", "all", "alter", "and", "as", "asc", "before", "between", "by", "call", "cascade",
    "case", "change", "char", "character", "check", "collate", "column", "condition",
    "constraint", "continue", "convert", "create", "cross", "current_date", "current_time",
    "current_timestamp", "current_user", "cursor", "database", "databases", "day_hour",
    "day_microsecond", "day_minute", "day_second", "dec", "decimal", "declare", "default",
    "delayed", "delete", "desc", "describe", "distinct", "div", "double", "drop", "dual",
    "each", "else", "elseif", "enclosed", "escaped", "exists", "exit", "explain", "false",
    "fetch", "float", "for", "force", "foreign", "from", "fulltext", "grant", "group",
    "having", "high_priority", "if", "ignore", "in", "index", "infile", "inner", "inout",
    "insensitive", "insert", "int", "integer", "interval", "into", "is", "iterate", "join",
    "key", "keys", "kill", "leading", "leave", "left", "like", "limit", "lines", "load",
    "localtime", "localtimestamp", "lock", "long", "longblob", "longtext", "loop",
    "low_priority", "match", "mediumblob", "mediumint", "mediumtext", "mod", "modifies",
    "natural", "not", "null", "numeric", "on", "optimize", "option", "optionally", "or",
    "order", "out", "outer", "outfile", "precision", "primary", "procedure", "purge",
    "range", "read", "reads", "real", "references", "regexp", "release", "rename", "repeat",
    "replace", "require", "restrict", "return", "revoke", "right", "rlike", "schema",
    "schemas", "select", "sensitive", "separator", "set", "show", "smallint", "spatial",
    "specific", "sql", "sqlexception", "sqlstate", "sqlwarning", "ssl", "starting",
    "straight_join", "table", "terminated", "then", "tinyblob", "tinyint", "tinytext", "to",
    "trailing", "trigger", "true", "undo", "union", "unique", "unlock", "unsigned", "update",
    "usage", "use", "using", "utc_date", "utc_time", "utc_timestamp", "values", "varbinary",
    "varchar", "varcharacter", "varying", "when", "where", "while", "with", "write", "xor",
    "year_month", "zerofill", "rank", "row", "rows", "groups", "lead", "lag", "over",
    "window", "system", "cume_dist", "dense_rank", "first_value", "last_value", "nth_value",
    "ntile", "percent_rank", "recursive", "of", "empty", "json_table", "lateral",
}


def q_ident(name):
    """反引号包标识符，内部反引号翻倍。始终加引号——保留字和中文字段名都不用特判。"""
    return "`" + str(name).replace("`", "``") + "`"


def q_comment(text, max_bytes=1024):
    """转义并按字节截断列注释。MySQL 列注释上限 1024 字符，这里按字节保守处理。"""
    if not text:
        return None
    s = " ".join(str(text).split())
    s = s.replace("\\", "\\\\").replace("'", "\\'")
    encoded = s.encode("utf-8")
    if len(encoded) > max_bytes:
        s = encoded[:max_bytes].decode("utf-8", "ignore")
    return "'" + s + "'"


def _dedupe_columns(columns, warnings):
    """MySQL 不允许重名列。保留首次出现，其余丢弃并告警。"""
    seen, out = {}, []
    for name, htype, comment in columns:
        key = name.lower()
        if key in seen:
            warnings.append("列 %s 重复出现，已保留第一次定义（丢弃后续 %s %s）" % (name, name, htype))
            continue
        seen[key] = True
        out.append((name, htype, comment))
    return out


def build_mysql_ddl(parsed, args):
    """产出 (ddl_text, rows, warnings)。rows 用于核对报告。"""
    warnings = list(parsed.get("unparsed_warnings") or [])
    for spec in parsed.get("unparsed") or []:
        warnings.append("以下定义没被识别为列，已跳过：%s" % " ".join(spec.split())[:200])

    columns = list(parsed["columns"])

    # 分区字段：Hive 的分区列在 MySQL 没有对应概念，按 --partition-mode 处理
    part_names = []
    if parsed["partitions"]:
        if args.partition_mode == "drop":
            for name, htype, _c in parsed["partitions"]:
                warnings.append("分区字段 %s (%s) 按 --partition-mode drop 已丢弃" % (name, htype))
        else:
            existing = {c[0].lower() for c in columns}
            for name, htype, comment in parsed["partitions"]:
                if name.lower() in existing:
                    warnings.append("分区字段 %s 与普通列重名，未重复添加" % name)
                    continue
                columns.append((name, htype, comment))
                part_names.append(name)

    columns = _dedupe_columns(columns, warnings)

    table_name = args.table or parsed["table"]
    if not table_name:
        raise SystemExit("解析不到表名，请用 --table 指定。")
    db_name = resolve_target_db(args)
    if args.strip_db:
        db_name = None
    if parsed["db"] and db_name and parsed["db"].lower() != db_name.lower():
        warnings.append("源 Hive 库名 %s 未沿用，MySQL 目标库取 %s（--db 可覆盖）"
                        % (parsed["db"], db_name))
    full_name = "%s.%s" % (q_ident(db_name), q_ident(table_name)) if db_name else q_ident(table_name)

    # 主键列必须 NOT NULL——MySQL 会静默把 PK 列改成 NOT NULL，
    # 那样 DDL 写的 DEFAULT NULL 和建出来的表就不一致，容易误导人。这里显式写出来。
    pk_set = set()
    if args.primary_key:
        pk_set = {k.strip().lower() for k in args.primary_key.split(",") if k.strip()}
    not_null_set = {n.lower() for n in (args.not_null or [])} | pk_set

    rows, col_lines = [], []
    for name, htype, comment in columns:
        mysql_type, note, warn = map_type(name, htype, args)
        if warn:
            warnings.append("列 %s：%s" % (name, warn))
        if name.lower() in _MYSQL_RESERVED:
            warnings.append("列 %s 是 MySQL 保留字，已用反引号包裹（下游查询也需带反引号）" % name)

        piece = "  %s %s" % (q_ident(name), mysql_type)
        if name.lower() in not_null_set:
            piece += " NOT NULL"
        else:
            piece += " DEFAULT NULL"
        c = q_comment(comment, args.comment_max_bytes)
        if c:
            piece += " COMMENT " + c
        col_lines.append(piece)

        rows.append({
            "字段": name,
            "Hive 类型": htype,
            "MySQL 类型": mysql_type,
            "注释": comment,
            "映射说明": note,
            "来源": "分区字段(降级为普通列)" if name in part_names else "普通列",
        })

    # 主键：仅在显式指定时输出。猜出来的主键会静默丢数据（重复行被拒），不能默认给。
    tail_lines = []
    if args.primary_key:
        pk_cols = [k.strip() for k in args.primary_key.split(",") if k.strip()]
        known = {c[0].lower(): c[0] for c in columns}
        missing = [k for k in pk_cols if k.lower() not in known]
        if missing:
            raise SystemExit("--primary-key 里的字段不在列清单里：%s" % ", ".join(missing))
        tail_lines.append("  PRIMARY KEY (%s)" % ", ".join(q_ident(known[k.lower()]) for k in pk_cols))

    for spec in args.index or []:
        idx_cols = [c.strip() for c in spec.split(",") if c.strip()]
        known = {c[0].lower(): c[0] for c in columns}
        missing = [c for c in idx_cols if c.lower() not in known]
        if missing:
            raise SystemExit("--index 里的字段不在列清单里：%s" % ", ".join(missing))
        real = [known[c.lower()] for c in idx_cols]
        idx_name = "idx_" + "_".join(re.sub(r"[^\w]+", "_", c) for c in real)
        # TEXT/BLOB 列做索引必须给前缀长度，否则 MySQL 直接报错
        type_by_name = {r["字段"].lower(): r["MySQL 类型"] for r in rows}
        parts = []
        for c in real:
            t = type_by_name.get(c.lower(), "")
            if t.upper().endswith("TEXT") or t.upper().endswith("BLOB"):
                parts.append("%s(191)" % q_ident(c))
                warnings.append("索引列 %s 是 %s，已加前缀长度 191" % (c, t))
            else:
                parts.append(q_ident(c))
        tail_lines.append("  KEY %s (%s)" % (q_ident(idx_name[:64]), ", ".join(parts)))

    body = ",\n".join(col_lines + tail_lines)

    header = "CREATE TABLE "
    if not args.no_if_not_exists:
        header += "IF NOT EXISTS "
    header += full_name + " (\n"

    ddl = header + body + "\n)"
    ddl += " ENGINE=%s" % args.engine
    ddl += " DEFAULT CHARSET=%s" % args.charset
    if args.collate:
        ddl += " COLLATE=%s" % args.collate
    tc = q_comment(args.table_comment if args.table_comment is not None else parsed["table_comment"],
                   args.comment_max_bytes)
    if tc:
        ddl += " COMMENT=" + tc
    ddl += ";"

    return ddl, rows, warnings


# ---------------------------------------------------------------- 报告 / 落盘

def _timestamp():
    return _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")


def output_dir(args):
    if args.out_dir:
        return args.out_dir
    return os.path.join(_project_root(), "docs", "hive-ddl-to-mysql")


def write_outputs(ddl, rows, warnings, parsed, args, executed):
    d = output_dir(args)
    os.makedirs(d, exist_ok=True)
    table = args.table or parsed["table"] or "table"
    safe = re.sub(r"[^\w.\-]+", "_", table)
    ts = _timestamp()

    sql_path = os.path.join(d, "%s_%s.sql" % (safe, ts))
    with open(sql_path, "w", encoding="utf-8") as fh:
        fh.write("-- MySQL 建表语句，由 hive-ddl-to-mysql 从 Hive DDL 转换生成\n")
        fh.write("-- 生成时间：%s\n" % _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        fh.write("-- 列数：%d；转换告警：%d 条\n" % (len(rows), len(warnings)))
        fh.write("-- 已在 MySQL 上执行：%s\n\n" % ("是" if executed else "否（仅生成语句）"))
        fh.write(ddl + "\n")

    md_path = os.path.join(d, "%s_%s_字段核对报告.md" % (safe, ts))
    buf = io.StringIO()
    buf.write("# %s · Hive → MySQL 字段核对报告\n\n" % table)
    buf.write("- 生成时间：%s\n" % _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    buf.write("- 列数：**%d**（其中分区降级列 %d）\n"
              % (len(rows), sum(1 for r in rows if r["来源"].startswith("分区"))))
    buf.write("- 是否已在 MySQL 执行：**%s**\n" % ("是" if executed else "否，仅生成语句"))
    buf.write("- 转换告警：**%d 条**\n\n" % len(warnings))

    buf.write("## 字段映射明细\n\n")
    buf.write("| # | 字段 | Hive 类型 | MySQL 类型 | 注释 | 映射说明 | 来源 |\n")
    buf.write("| --- | --- | --- | --- | --- | --- | --- |\n")
    for i, r in enumerate(rows, 1):
        buf.write("| %d | `%s` | %s | **%s** | %s | %s | %s |\n" % (
            i, r["字段"], r["Hive 类型"] or "-", r["MySQL 类型"],
            (r["注释"] or "-").replace("|", "\\|"),
            (r["映射说明"] or "-").replace("|", "\\|"), r["来源"]))

    heur = [r for r in rows if r["映射说明"]]
    if heur:
        buf.write("\n## 需人工确认：按字段名启发式推断的类型（%d 个）\n\n" % len(heur))
        buf.write("Hive `string` 不带长度，转 MySQL 必须选一个具体类型，以下是脚本按字段名猜的。"
                  "**猜短了会截断数据**，请逐条过一遍。\n\n")
        for r in heur:
            buf.write("- `%s` → **%s** —— %s\n" % (r["字段"], r["MySQL 类型"], r["映射说明"]))

    if warnings:
        buf.write("\n## 转换告警（%d 条）\n\n" % len(warnings))
        for w in warnings:
            buf.write("- %s\n" % w)

    buf.write("\n## 完整 DDL\n\n```sql\n%s\n```\n" % ddl)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())

    csv_path = os.path.join(d, "%s_%s_字段映射.csv" % (safe, ts))
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["字段", "Hive 类型", "MySQL 类型", "注释", "映射说明", "来源"])
        w.writeheader()
        w.writerows(rows)

    return sql_path, md_path, csv_path


# ---------------------------------------------------------------- 执行

def execute_ddl(ddl, args, parsed):
    """连 MySQL 执行建表。只在 --execute 时调用。"""
    try:
        import pymysql
    except ImportError:
        raise SystemExit("需要 pymysql 才能执行建表：pip3 install pymysql（或去掉 --execute 只生成语句）")

    conn_args, cfg_path = resolve_conn(args)
    missing = [k for k in ("host", "user") if not conn_args.get(k)]
    if missing:
        raise SystemExit(
            "缺少连接参数：%s。请照 config.example.yml 建好 config.yml（当前找到的配置：%s），"
            "或用 --host/--user/--password 传入。" % (", ".join(missing), cfg_path or "无"))
    if not conn_args.get("password"):
        print("[warn] 未提供密码，将以空密码尝试连接", file=sys.stderr)

    # 与生成 DDL 时同一套优先级，保证「打印出来的库」和「实际建到的库」一致。
    target_db = resolve_target_db(args) or conn_args.get("database")
    if args.strip_db:
        target_db = conn_args.get("database")

    print("[exec] 连接 %s@%s:%s db=%s" % (
        conn_args["user"], conn_args["host"], conn_args["port"], target_db or "-"), file=sys.stderr)

    conn = pymysql.connect(
        host=conn_args["host"], port=conn_args["port"], user=conn_args["user"],
        password=conn_args.get("password") or "", charset=conn_args.get("charset") or "utf8mb4",
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            if target_db:
                if args.create_database:
                    cur.execute("CREATE DATABASE IF NOT EXISTS %s DEFAULT CHARSET %s"
                                % (q_ident(target_db), args.charset))
                    print("[exec] 已确保库存在：%s" % target_db, file=sys.stderr)
                cur.execute("USE %s" % q_ident(target_db))
            cur.execute(ddl)
            print("[exec] 建表语句执行成功", file=sys.stderr)

            table = args.table or parsed["table"]
            cur.execute("SHOW TABLES LIKE %s", (table,))
            exists = cur.fetchone() is not None
            cols = 0
            if exists:
                cur.execute("SELECT COUNT(*) FROM information_schema.columns "
                            "WHERE table_schema = COALESCE(%s, DATABASE()) AND table_name = %s",
                            (target_db, table))
                cols = cur.fetchone()[0]
            return {"ok": True, "exists": exists, "columns": cols, "database": target_db}
    finally:
        conn.close()


# ---------------------------------------------------------------- CLI

def read_input(args):
    if args.ddl_file == "-":
        return sys.stdin.read()
    if args.ddl_file:
        if not os.path.isfile(args.ddl_file):
            raise SystemExit("文件不存在：%s" % args.ddl_file)
        with open(args.ddl_file, "r", encoding="utf-8") as fh:
            return fh.read()
    if args.ddl:
        return args.ddl
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("没有输入。用 --ddl-file <路径> 或 --ddl '<语句>'，或从标准输入喂进来。")


def main(argv=None):
    p = argparse.ArgumentParser(
        description="把 Hive/MaxCompute 建表语句转成 MySQL 建表语句；默认只生成，不执行。")
    src = p.add_argument_group("输入")
    src.add_argument("--ddl-file", help="Hive DDL 文件路径，'-' 表示标准输入")
    src.add_argument("--ddl", help="直接传 Hive DDL 字符串")
    src.add_argument("--format", choices=["auto", "create", "describe"], default="auto",
                     help="输入格式：auto 自动判断；describe 按「字段 类型 注释」三列文本解析")

    tgt = p.add_argument_group("目标表")
    tgt.add_argument("--table", help="覆盖目标表名")
    tgt.add_argument("--db", help="覆盖目标库名（默认取 config.yml 的 mysql.database，"
                                  "不沿用 Hive DDL 里的源库名）")
    tgt.add_argument("--strip-db", action="store_true", help="去掉库名前缀，只用裸表名")
    tgt.add_argument("--table-comment", help="覆盖表注释")
    tgt.add_argument("--engine", default="InnoDB")
    tgt.add_argument("--charset", default="utf8mb4")
    tgt.add_argument("--collate", default="", help="如 utf8mb4_general_ci，默认不输出")
    tgt.add_argument("--no-if-not-exists", action="store_true",
                     help="不输出 IF NOT EXISTS（表已存在时会报错）")

    mp = p.add_argument_group("类型映射")
    mp.add_argument("--varchar-len", type=int, default=255,
                    help="string 无字段名信号时的默认 VARCHAR 长度（默认 255）")
    mp.add_argument("--varchar-max", type=int, default=2048,
                    help="超过此长度的 varchar 改用 TEXT（默认 2048）")
    mp.add_argument("--string-type", default="",
                    help="强制所有 string 都用这个类型，如 TEXT / VARCHAR(512)，关闭字段名启发式")
    mp.add_argument("--decimal-default", default="20,6",
                    help="Hive decimal 不带精度时用的精度（默认 20,6）")
    mp.add_argument("--complex-type", default="JSON",
                    help="array/map/struct 落成的类型（默认 JSON，可给 TEXT）")
    mp.add_argument("--comment-max-bytes", type=int, default=1024)

    st = p.add_argument_group("结构")
    st.add_argument("--partition-mode", choices=["column", "drop"], default="column",
                    help="Hive 分区字段处理：column 降级为普通列（默认）；drop 丢弃")
    st.add_argument("--primary-key", help="主键列，逗号分隔。默认不加主键")
    st.add_argument("--index", action="append",
                    help="加普通索引，逗号分隔为联合索引；可重复")
    st.add_argument("--not-null", action="append",
                    help="指定 NOT NULL 的列；可重复。默认所有列 DEFAULT NULL")

    ex = p.add_argument_group("执行（默认不执行）")
    ex.add_argument("--execute", action="store_true", help="连 MySQL 真的执行建表")
    ex.add_argument("--create-database", action="store_true",
                    help="执行前 CREATE DATABASE IF NOT EXISTS")
    ex.add_argument("--config", help="config.yml 路径")
    ex.add_argument("--host")
    ex.add_argument("--port")
    ex.add_argument("--user")
    ex.add_argument("--password")
    ex.add_argument("--database")

    out = p.add_argument_group("输出")
    out.add_argument("--out-dir", help="产物目录，默认 my_skills/docs/hive-ddl-to-mysql/")
    out.add_argument("--no-save", action="store_true", help="只打到 stdout，不落盘")

    args = p.parse_args(argv)

    raw = read_input(args)
    if not raw.strip():
        raise SystemExit("输入为空。")

    fmt = args.format
    if fmt == "auto":
        clean = _strip_sql_comments(raw)
        if _CREATE_RE.search(clean):
            fmt = "create"
        elif re.search(r"(?i)\b(select|insert|update|delete|drop|alter)\b", clean):
            # 明显是别的 SQL，别硬套 describe 解析出一堆假字段
            raise SystemExit(
                "输入看起来是一段 SQL 查询/DDL，但不是 CREATE TABLE 语句。"
                "本技能只接受 Hive/ODPS 建表语句，或 describe 输出（--format describe）。")
        else:
            fmt = "describe"
    parsed = parse_hive_ddl(raw) if fmt == "create" else parse_describe(raw)

    ddl, rows, warnings = build_mysql_ddl(parsed, args)

    print(ddl)

    executed = None
    if args.execute:
        executed = execute_ddl(ddl, args, parsed)

    if not args.no_save:
        sql_path, md_path, csv_path = write_outputs(
            ddl, rows, warnings, parsed, args, bool(executed))
        print("\n-- 建表语句：%s" % sql_path, file=sys.stderr)
        print("-- 核对报告：%s" % md_path, file=sys.stderr)
        print("-- 字段映射 CSV：%s" % csv_path, file=sys.stderr)

    heur = sum(1 for r in rows if r["映射说明"])
    print("\n-- 汇总：%d 列，其中 %d 列类型由字段名启发式推断，%d 条告警"
          % (len(rows), heur, len(warnings)), file=sys.stderr)
    for w in warnings:
        print("-- [warn] %s" % w, file=sys.stderr)
    if executed:
        print("-- 已在 MySQL 建表：db=%s exists=%s columns=%d"
              % (executed["database"], executed["exists"], executed["columns"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
