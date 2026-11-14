# LLM Model Name Length Comparison

[English](README.en.md)

输入 API 请求名，统计其字符数。 命令行工具，支持单条、批量、规则配置和中英文输出。当前版本 **0.3.0**。

## 快速使用

需要 **Python 3.10 或更新版本**，运行时仅使用标准库。进入本项目目录后执行：

```console
python3 benchmark.py "model-agi"
模型名称长度: 9
```

```console
python3 benchmark.py "model-agi" --lang en
LLM Model Name Length: 9
```

不传名称时，在终端按提示输入；有管道输入时自动按批量输入读取。默认语言为中文，不依赖操作系统语言。

## 一致的输入方式

所有命令示例都给每个请求名加双引号。Shell 会去掉命令行的引号；脚本也会统一处理交互输入、文本行或 JSON 值中实际存在的外围引号。

1. 去掉外围空白。
2. 若存在一层匹配的 ASCII 单引号或双引号，去掉这层引号及包裹内容的外围空白。
3. 拒绝空名称、不匹配或嵌套的外围引号、控制字符、不可见格式字符及无效代理码点。
4. 完整保留名称内部的前缀、版本号和后缀，然后交给计分规则。

`model-agi`、`"model-agi"`、`'model-agi'` 的实际测评名称均为 `model-agi`。JSON 中同时记录原始 `input` 和实际测评 `name`。以 `-` 开头的名称应放在选项结束标记后：

```bash
python3 benchmark.py --lang en -- "-model-agi"
```

## 批量输入与 JSON

```bash
python3 benchmark.py "model-a" "model-gi" "model-agi"
python3 benchmark.py --input examples/models.txt
python3 benchmark.py --input examples/models.json --format json --lang en
python3 benchmark.py --input - --input-format json --format json
```

文本文件每行一个名称，空行忽略，支持 UTF-8 BOM 和 Windows 换行。JSON 输入是字符串数组。位置参数与 `--input` 不可同时使用。批量保留输入顺序和重复项；坏记录会返回逐条错误，其余记录继续评测。

输出格式为 `text` 或 `json`，均写入标准输出。文本错误与交互提示写入标准错误；JSON 错误放在 JSON 文档中。评测结果不自动保存，需要保存时可以使用命令行重定向；日志由 `--log-dir` 显式开启。

## 计分规则

默认排除普通空格 U+0020，按 Unicode 码点计数，不做 Unicode 归一化。连字符、数字、小数点、前缀、版本号和后缀均计入。`model-agi` 为 9，`模型-v1` 为 5，`e` 加组合重音符为 2。

| 选项 / 配置字段 | 默认值 | 可选值 |
| --- | --- | --- |
| `--spaces` / `spaces` | `ascii` | `ascii` 排除普通空格；`all` 排除所有 Unicode 空白；`none` 保留空格 |
| `--normalization` / `normalization` | `none` | `none`、`NFC`、`NFKC` |
| `--count` / `count` | `codepoints` | `codepoints` 统计码点；`utf8-bytes` 统计 UTF-8 字节 |

```bash
python3 benchmark.py "model v1.0" --spaces none
python3 benchmark.py "模型-v1" --count utf8-bytes --lang en
```

外围空白和成对引号属于输入包装，会在计分前处理。`--spaces none` 保留的是名称内部的空格。控制字符（例如内部制表符）始终被拒绝，`--spaces all` 不会放宽输入校验。JSON 会列出实际计数字符串、移除的空格数、码点数及 UTF-8 字节数。

## 配置文件

```bash
python3 benchmark.py "model-gi" --config examples/rules.json --format json
```

配置结构为 `{"schema_version":"1.0","benchmark":"llm-model-name-length-comparison","rules":{...}}`。`schema_version` 可省略，默认 `1.0`。配置必须先自身有效，再由命令行明确指定的规则覆盖；剩余规则使用默认值。未知字段、重复 JSON 字段和错误类型会报错。

实际规则与默认规则不同，文本会标记“自定义规则”，JSON 的 `rules.profile` 为 `custom`。默认配置为 `standard`。

## 结果与复现

JSON 文档包含：

- `schema_version`、`benchmark`、程序 `version` 和文案 `language`。
- 实际生效的 `rules` 及版本化的 `input_policy`。
- Python 和 Unicode 数据版本 `runtime`。
- 成功、失败及总数 `summary`。
- 每条原始输入、规范化名称、得分、单位、计算明细或稳定错误码 `results`。

相同输入、规则、程序和运行环境产生相同 JSON；输出不加入随机值或当前时间。不同语言仅改变文案，不改变分数、规则或错误码。运行环境不同可能影响 Unicode 行为，因此结果记录运行环境版本。

单条名称上限为 4096 个码点；每批最多 10000 条；输入文件最多 8 MiB；配置文件最多 64 KiB。文件解码、非法 JSON、输入来源冲突等整体错误会终止本次评测。

| 退出码 | 含义 |
| --- | --- |
| `0` | 全部成功 |
| `1` | 至少一条记录无效，或校验模式发现内容不一致 |
| `2` | 参数、配置、文件读取或日志写入错误 |
| `130` | 用户中断 |

## 双层哈希校验（0.3.0）

每份 JSON 评测结果自动附带 `integrity`，生成后立即自行验证。文本模式使用 `--hash` 显示两层摘要：

```bash
python3 benchmark.py "model-agi" --hash
python3 benchmark.py "model-agi" --format json > result.json
python3 benchmark.py --verify "result.json"
python3 benchmark.py --verify "result.json" --lang en --format json
```

两层含义固定为：

1. `payload_sha256`：对结果正文的规范 JSON 字节计算 SHA-256；正文包含输入、得分、规则、文案语言及软件/运行环境版本，排除顶层 `integrity` 字段。
2. `hash_sha256`：对第一层摘要的 **64 个小写十六进制 ASCII 字符**再次计算 SHA-256。

规范编码标识为 `json-sorted-ascii-v1`：Python `json.dumps(sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)`，再以 UTF-8 编码。不是 RFC 8785/JCS；规范及 Python 版本都记录在结果中。对象字段顺序和显示缩进不会影响校验，数组顺序、数字表示及内容变化可能影响校验。

`--verify` 同时检查两层摘要、元数据格式及所属 Benchmark。它验证内容一致性，不重新评测模型或计算得分。修改文案语言会改变整个结果的哈希，但不会改变分数。哈希均为未签名校验值：如果内容和全部摘要被一起重算，校验仍会通过；第二层不能替代可信签名或外部保留的摘要。

## 执行日志和“日志的日志”（0.3.0）

显式指定 `--log-dir` 开启日志，每次运行使用时间加随机 ID 创建独立目录：

```bash
python3 benchmark.py "model-a" "model-agi" --hash --log-dir "logs"
python3 benchmark.py --verify-logs "logs/RUN_ID"
python3 benchmark.py --verify-logs "logs/RUN_ID" --lang en --format json
```

将示例中的 `RUN_ID` 替换为运行结束时标准错误中显示的目录名。

| 文件 | 内容 |
| --- | --- |
| `execution.jsonl` | 运行开始、实际规则、输入数量、每条结果或错误、结果摘要验证、运行结束 |
| `audit.jsonl` | 每条执行日志成功写入后的记录：对应序号、事件、字节数和写入内容的 SHA-256 |
| `manifest.json` | 完成标记：两份日志的记录数、字节数、两层文件摘要、运行结果和对应的结果摘要；该文件本身也有两层哈希 |

执行日志每次写入都执行 `flush`、`fsync` 并读回核对；成功后才写对应的外层审计记录。两份日志中的每条记录都带两层摘要及前一行的 SHA-256，形成各自的顺序链。外层审计日志直接写入，不会继续生成第三层日志。

`--verify-logs` 校验完成清单、整份文件的两层摘要、每条记录的两层摘要、记录序号与顺序链、两份日志的一一对应关系，以及开始/结束记录。可以发现未同步修改摘要的内容变化，以及记录删除、重排、遗漏审计记录等一致性问题。**日志验证成功不等于原评测全部成功**：返回的 `outcome` 和 `original_exit_code` 保留原评测结果。

日志写入或读回失败会终止运行，返回退出码 2，不会发布正常成功结果。故障期间不递归记录日志故障；未完成目录可能留在磁盘上，没有有效的 `manifest.json` 时不能通过验证。参数解析成功后才开始记录，`--help`、解析失败及校验模式不会生成日志。两份日志各限制为 128 MiB。

日志包含名称、实际规则、错误和得分，以及 UTC 时间和运行 ID。开启日志不会把这些动态字段加入结果正文，因而不会改变同一次评测的确定性结果或结果哈希。日志只记录本地运行；结果仍写到标准输出，日志路径写到标准错误。

校验模式只能搭配 `--lang`、`--format`、`--hash`，与评测输入、规则和 `--log-dir` 分开使用。验证通过返回 0；哈希或日志一致性失败返回 1；参数或文件读取错误返回 2。

## 安装与开发

直接运行脚本即可。也可在自己的虚拟环境中安装命令行入口：

```bash
python3 -m pip install .
name-length "model-agi" --lang en
```

构建使用 setuptools，运行时没有第三方依赖。每个项目自带全部代码，可独立安装或复制。

```bash
python3 -m name_length_benchmark "model-agi" --lang en
python3 -m unittest discover -s tests -v
```

目录分为 `core.py`（计分）、`inputs.py`（输入与配置）、`i18n.py`（翻译）、`cli.py`（命令行）、`integrity.py`（两层哈希）、`execution_log.py`（执行与审计日志）和 `tests/`（验证实际行为）。增加语言时，补充翻译表及 `--lang` 支持列表，并检查翻译键和占位符一致性。

0.2.0 增加了统一输入规范和中文默认结果文案。原先可导入的 `benchmark.score(name)` 仍保留原始默认计分行为，直接处理传入字符串；CLI 则先执行上面的输入规范化。

## 许可证

本项目按 [MIT License](LICENSE) 开源。
