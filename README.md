# 设备停机叙述与维修事件归并

本地文本工作区用于保存设备及故障提及识别相关的合成记录，并提供基础规范化和词项查看能力。

## 环境

需要 Python 3.11 或更新版本。运行程序仅使用 Python 标准库。测试依赖固定为 pytest 8.3.4，可执行 `python -m pip install -r requirements-dev.txt` 安装。

## 运行

从项目根目录执行 `PYTHONPATH=src python -m nlp_maintenance_event_desk.cli ingest fixtures/records.json` 导入样例，随后执行 `PYTHONPATH=src python -m nlp_maintenance_event_desk.cli list` 查看记录。工作数据写入 `var/records.json`。业务查询使用 fixtures/business.json 中的初始资料，约定见 fixtures/business-contract.md。执行 `PYTHONPATH=src python -m nlp_maintenance_event_desk.cli business` 可查看当前业务目录结果。

样例为本地合成文本。记录包含来源、修订号和原文，当前工作区对空白做统一处理，并提供字符及英文词项切分。

## 交接排产核对

`PYTHONPATH=src python -m nlp_maintenance_event_desk.shift_cli` 查看各设备当前排产资格。系统并排核对两类来源：fixtures/history.json 中已核对的技术记录（设备号、发生时刻、停机事件号齐全），以及 fixtures/records.json 中技术员的中文交接记录。中文记录里“东侧那台”这类设备指代、“08:15”“10:40 之后”“上一次复位前”这类不精确时间，都先解析为带候选和约束的观察；又停、短暂跑过（试运行、复位）和经当班检验人签认恢复是三件独立的事。

记录的到达次序与事件次序可以不同。系统枚举现有证据下全部可信解释（设备归属 × 满足时间约束的事件次序），只有所有解释都支持当前一次停机已解除（停机后先试运行、再由名册上当班检验人签认），设备才可排产。两台设备同班报过相似症状时，一次复位加签认在一个解释里只能落在一台设备上，不会让两台都恢复排产。

解释之间结论不一致的设备进入待核，并区分不确定来自设备身份还是试运行与签认的先后；需要维护人员确认的记录段随计划列出。全部解释都支持恢复但签认依据不同时，差异保留在 `clearance_options` 供回查。未采信的签认（检验人不在当班名册、先后关系不成立）也逐条说明原因。

`PYTHONPATH=src python -m nlp_maintenance_event_desk.shift_cli issue` 为当前可排产的设备签发交接单，写入 `var/handovers.json`；交接单看得见当时采用的设备和事件，结论未变不重复签发。`... shift_cli sheets` 查看已签发的交接单。新一轮排产只采用现在有依据的确认结果：晚到的记录（例如 `plan --records fixtures/records.json fixtures/records-late.json`）若使已交班设备进入待核，计划会标出需要重新排产的设备、仍可执行的安排，以及需要维护人员确认的记录段。

## 测试

执行 `python -m pytest -q` 检查规范化、记录持久化、来源筛选、中文记录核对、多解释排产结论和交接单签发。
