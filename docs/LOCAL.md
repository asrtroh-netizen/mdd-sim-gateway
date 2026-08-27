# 本机限制（local.yaml）

这四个开关只作用于**本机自己的 SIM**，不做成单独的解锁页。值保存在
`$MDD_DATA/local.yaml`（与 `config.yaml` 同一数据目录），也可以在
**系统设置 → 常规** 里改；保存时会同时写入设置镜像和 `local.yaml`。
重新打开设置页会显示当前生效值。

没有 `local.yaml` 时保持原来的产品默认：最多 5 条线路，独立 SIP、Telegram
远程命令、持久 Asterisk 调试全部关闭。

## 如何启用

```bash
# 不要覆盖已经改过的文件
cp -n examples/local.yaml "$MDD_DATA/local.yaml"
# 若安装器用的是默认数据目录：
#   cp -n examples/local.yaml /path/to/mdd-sim-gateway/data/local.yaml
sudo ./install.sh reload
```

`install.sh` 在创建 `$MDD_DATA` 时，若该文件还不存在，会把 `examples/local.yaml`
复制过去；已有文件不会被覆盖。

示例文件里四项都是打开的（线路上限 8，绝对天花板 32）。也可只改需要的项：

| 字段 | 设置页标签 | 默认 | 作用 |
|---|---|---|---|
| `max_sim_lines` | 最多 SIM 线路 | 5 | `upsert_instance` / `line_allowed` 使用的上限 |
| `allow_external_sip` | 允许独立 SIP 话机/中继 | false | 保留并渲染 `sip.external` |
| `allow_telegram_commands` | 允许 Telegram 远程命令 | false | 保留 `telegram.commands`，对本机线路执行 /status /sms /call /hangup |
| `persist_asterisk_debug` | 允许保存 Asterisk 调试 | false | 保存并下发 `debug.asterisk` |

坏类型或缺失文件会回退到默认值；`max_sim_lines` 大于 32 会被夹到 32。

Telegram 命令只接受 Settings 里已配置的 bot token / chat_id，并且只操作
`line_allowed()` 通过的本机线路。User-Agent 仍是 `MDD-Sim-Gateway`，不读取 Ki/OP/OPc，
不改变国家出口的 fail-closed 行为。

## 跑测试

```bash
python -m unittest tests.test_product_boundaries tests.test_local_yaml tests.test_notify_commands
python -m unittest discover -s tests
```
