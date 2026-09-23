---
name: hive-ddl-to-mysql-skill
description: 把 Hive / MaxCompute(ODPS) 的 CREATE TABLE 语句转成 MySQL 建表语句，并可按要求直接连 MySQL 执行建表。当用户说「把这个 hive 建表语句转成 mysql 的」「这张 hive 表在 mysql 里怎么建」「hive 表结构同步到 mysql」「按这个 DDL 在 mysql 建张表」「帮我在 mysql 里建这张表」「Hive DDL 转 MySQL DDL」时使用。自动做类型映射（string→varchar/text、decimal 保精度、boolean→tinyint(1)、array/map/struct→json）、保留每列中文 COMMENT、反引号包裹保留字、把 Hive 分区字段降级成普通列。默认【只生成语句不动数据库】，只有用户明确要求建表时才加 --execute 连库执行；连接信息从 config.yml 的 mysql 段读取。同时产出字段核对报告，把按字段名猜出来的 varchar 长度单列出来供复核。不负责在 Hive 上跑 SQL 导出数据（那是 hive-sql-to-csv），不负责查字段字典补中文注释生成 ODPS 建表语句（那是 dataworks-sql-table 的 ddl 子命令），也不负责追字段血缘（那是 sql-field-lineage）。
version: 1.0.0
---

# hive-ddl-to-mysql-skill

给一段 Hive/ODPS 的 `CREATE TABLE`，产出一条可直接执行的 MySQL `CREATE TABLE`，
每列保留原有的中文 `COMMENT`，并附一份字段映射核对报告。用户明确要求时才真的连库建表。

典型场景：「这张 Hive 表要同步到 MySQL，帮我把建表语句写出来」。

这件事的难点不在改几个类型名，而在**Hive 的类型系统比 MySQL 松**，转换必然要做取舍：

- `string` 没有长度，MySQL 必须选一个具体类型——**猜短了会截断数据**。
- `array/map/struct` 在 MySQL 没有对应物。
- Hive 的**分区字段**是独立概念，MySQL 的分区完全是另一回事。

所以脚本把每个取舍都记进报告，并把启发式推断的部分**单列出来**，而不是混在结果里假装确定。

## 前置：什么时候需要连接配置

**只生成语句不需要账号密码**——这是默认路径，直接就能跑。
生成语句时只会去 `config.yml` 取一个 `mysql.database` 当目标库名（见下），取不到就生成裸表名。

加 `--execute` 真的建表时才需要完整连接信息。它分两处，**密码不落盘**：

- **密码**：环境变量 `MYSQL_PASSWORD`，配在 `~/.claude/settings.json` 的 `env` 段。
- **其余非敏感项**（`host / port / username / database / charset`）：`config.yml` 的
  `mysql:` 段。查找顺序：`--config` 指定的路径 → 环境变量 `MYSQL_CONFIG` →
  **本仓库根**下的 `config.yml` → `~/.mysql/config.yml`。

CLI 参数（`--host/--user/--password/...`）和环境变量（`MYSQL_HOST/MYSQL_USER/MYSQL_PASSWORD/...`）
可覆盖任意字段，优先级 **CLI > 环境变量 > config.yml**。

脚本报「缺少连接参数」时，**不要**去猜 host / 账号密码，如实告诉用户，并按报错项分别提示：
缺密码 → 检查 `~/.claude/settings.json` 的 `env.MYSQL_PASSWORD`（改完要重开会话才生效），
缺其它字段 → 检查本技能目录下的 `config.yml`。

### 目标库名不沿用 Hive 库名

Hive 侧的库（`my_db`、`dw` 等）和 MySQL 侧的库是两套命名，把源库名照抄过来基本都是错的。
所以脚本**不用** Hive DDL 里解析出的库名，目标库固定走
**`--db` > 环境变量 `MYSQL_DATABASE` > `config.yml` 的 `mysql.database`**。
本仓库 `config.yml` 里配的是 `target_db`，因此默认产出的就是
`` `target_db`.`表名` ``，不是 `` `my_db`.`表名` ``。

源库名和目标库名不一致时会记一条告警，把这次「换库了」的事实摆在报告里。
用户明确要求落到别的库时用 `--db`；不想要库名前缀就 `--strip-db`。

## 怎么做

```bash
SK="<本技能目录>"

# ① 主路径：Hive DDL 落成文件再转（推荐，DDL 通常很长且带引号换行）
python3 "$SK/scripts/hive_ddl_to_mysql.py" --ddl-file /tmp/hive_ddl.sql

# ② DDL 很短时直接传
python3 "$SK/scripts/hive_ddl_to_mysql.py" --ddl "create table db.t (a string comment '甲')"

# ③ 用户明确说「建表 / 执行」时才加 --execute
python3 "$SK/scripts/hive_ddl_to_mysql.py" --ddl-file d.sql --execute --db mydb
python3 "$SK/scripts/hive_ddl_to_mysql.py" --ddl-file d.sql --execute --create-database --db newdb

# ④ 改表名/库名、加主键和索引
python3 "$SK/scripts/hive_ddl_to_mysql.py" --ddl-file d.sql \
  --table t_new --db mydb --primary-key order_no --index user_id,dt --not-null user_id

# ⑤ 不想要字段名启发式，所有 string 统一给一个类型
python3 "$SK/scripts/hive_ddl_to_mysql.py" --ddl-file d.sql --string-type 'VARCHAR(512)'

# ⑥ 只有 describe 输出、没有建表语句时
python3 "$SK/scripts/hive_ddl_to_mysql.py" --format describe --ddl-file cols.txt --table t
```

**用户在对话里贴了很长的 Hive DDL**：先用 Write 把它写成 `.sql` 文件再 `--ddl-file` 跑，
比拼一条巨长的 `--ddl` 命令行可靠得多（DDL 里的单引号中文注释很容易把命令行搞坏）。

**用户在 IDE 里打开了建表语句文件**（上下文里有该文件路径）而没另外贴 DDL 时，
默认就把那个文件当输入，跑之前确认一句。

## 默认不建表

`CREATE TABLE` 是写操作。默认只生成语句、落盘、打到 stdout，**不连数据库**。

只有用户**明确要求**（「帮我建出来」「在 mysql 里执行」「同步过去」）时才加 `--execute`。
用户只说「把建表语句转一下 / 写出来 / 给我看看」时，不要自作主张去建表。
不确定就先出语句，然后问一句要不要执行。

`--execute` 时的动作顺序：连库 →（`--create-database` 时）建库 → `USE` → 建表 →
回查 `information_schema` 确认表存在和列数。默认带 `IF NOT EXISTS`，所以**重复跑不会报错，
但也不会改已存在表的结构**——表已存在时列数可能和你刚生成的 DDL 不一致，
脚本汇报的列数就是用来暴露这件事的，发现不一致要如实说出来。

## 脚本行为要点

- **类型映射**：`bigint/long→BIGINT`、`int→INT`、`double→DOUBLE`、`boolean→TINYINT(1)`、
  `timestamp/datetime→DATETIME`、`date→DATE`、`binary→BLOB`。
  `decimal(20,6)` **保留精度**原样带过去；`decimal` 不带精度时按 `--decimal-default`（默认 `20,6`）。
- **`string` 按字段名启发式给长度**（只看字段名的**最后一个** token，即 snake_case 的语义中心词）：
  - 中心词是 `remark/desc/content/json/ext/url/address/reason/title/…` → `TEXT`
  - 中心词是 `id/no/code/time/date/type/status/flag/…` → `VARCHAR(64)`
  - 中心词是 `name/channel/source/city/study/…` → `VARCHAR(128)`
  - 其余 → `VARCHAR(255)`（`--varchar-len` 可调）
  只看最后一个 token 是刻意的：`no_comment_col` 里的 `comment` 是修饰语不是中心词，
  按任意位置匹配会把它错判成 `TEXT`。`--string-type` 可整体关掉这档启发式。
- **复杂类型**：`array/map/struct/uniontype` → `JSON`（`--complex-type TEXT` 可改），并告警。
  尖括号内的逗号不会把列切错（`map<string,string>` 是一列，不是两列）。
- **分区字段降级成普通列**：Hive 的 `PARTITIONED BY (dt string)` 在 MySQL 没有对应概念，
  默认把 `dt` 作为**普通列**追加到列尾并保留注释，**不建索引**。
  要按天查得快就自己加 `--index dt`。`--partition-mode drop` 可整体丢弃分区字段。
- **保留字与标识符**：所有列名/表名**一律反引号包裹**，所以 `order`、`key`、`desc`
  这类保留字和中文列名都不用特判；命中保留字时额外告警一句，提醒下游查询也得带反引号。
- **重名列**：MySQL 不允许重名列，保留第一次定义、丢弃后续并告警。
- **注释**：原样保留并转义（先转反斜杠再转单引号），换行压成空格，超 1024 字节按字符边界截断。
  含逗号和括号的中文注释能正确穿过解析（这是解析器最容易错的地方，已覆盖）。
- **主键与索引默认都不加**。猜出来的主键会静默拒绝重复行导致丢数据，必须 `--primary-key` 显式给。
  指定为主键的列会**自动补 `NOT NULL`**（否则 MySQL 静默改成 NOT NULL，DDL 和实际表结构不一致）。
  `--index` 里的 `TEXT/BLOB` 列会自动加前缀长度 191（不加 MySQL 直接报错）。
- **其余列默认 `DEFAULT NULL`**。Hive 表通常没有非空约束，硬加 NOT NULL 会让同步作业写失败。
  要非空用 `--not-null`（可重复）。
- **认不出的类型原样大写透传**，并在报告里单列告警——留给 MySQL 报错，比脚本瞎猜一个类型安全。

## 交付什么

DDL 同时打到 stdout（立即可用）和落盘，产物在 `docs/hive-ddl-to-mysql/`：

- `<表名>_<时间戳>.sql` —— MySQL 建表语句，头部注释记录生成时间、列数、告警数、是否已执行。
- `<表名>_<时间戳>_字段核对报告.md` —— 字段映射明细表（字段/Hive 类型/MySQL 类型/注释/映射说明/来源）、
  **需人工确认的启发式类型**单列小节、全部告警、以及完整 DDL（使报告自包含）。
- `<表名>_<时间戳>_字段映射.csv` —— 同样的映射表，方便贴进表格评审。

跑完在对话里精简汇报：列数、**有多少列的类型是按字段名猜的**、分区字段怎么处理的、
告警条数、产物路径；执行了的话再报一句库名和实际列数。

**必须把启发式推断的 varchar 长度念给用户看**，尤其是给了 `VARCHAR(64)` 的那些——
这是唯一会**静默截断生产数据**的一档，不能和确定的映射混为一谈。

## 说明与边界

- **`string` → 长度是猜的，猜短了会截断数据**。字段名启发式只是省事，不是权威依据。
  真实数据可能远超推断长度（比如 `remark` 型内容进了 `_name` 字段）。拿不准就
  `--string-type TEXT` 或把长度调大——MySQL 里 `varchar` 只按实际内容占空间，给宽一点几乎不亏。
- **不做数据迁移**，只管表结构。搬数据是另一回事（导出可用 `hive-sql-to-csv`）。
- **表已存在时不改结构**。默认 `IF NOT EXISTS` 只保证不报错，不会 ALTER 对齐字段；
  需要改结构请人工写 `ALTER TABLE`，脚本不生成也不执行 DDL 变更。
- **`array/map/struct` → JSON 需要上游配合**：MySQL 的 JSON 列要求写入合法 JSON 文本，
  Hive 侧直接把复杂类型 toString 出来的格式通常**不是**合法 JSON。落地前确认写入端格式，
  或改用 `--complex-type TEXT`。
- 本地只校验结构，校验不了目标 MySQL 的版本差异（`JSON` 需 5.7+，`utf8mb4` 索引长度限制等）。
  首次使用建议先在测试库跑一次。
- 不查字段字典补中文注释（那是 `dataworks-sql-table` 的 `ddl` 子命令，产出的是 ODPS 建表语句）；
  不在 Hive 上执行 SQL（那是 `hive-sql-to-csv`）；不追字段血缘（那是 `sql-field-lineage`）。
