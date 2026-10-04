# 武汉晚霞预报推送

只在高分日推送微信提醒。低分日静默，避免无效打扰。

## 功能

- 取 glowsunset.cn 的武汉晚霞预测，综合评分 ≥ 60（"很棒"级）时推送
- 每 6 小时检查一次；日落前 4 小时至 45 分钟内若仍达线，加推一条提醒出门
- 未来出现 ≥ 85（"绝美"级）时提前一天预告
- 用 Open-Meteo 三套数值模式（EC / CMA / GFS）交叉校验低云量，分歧大时在推送中标警告
- 去重：同一事件当天只推一次
- 静默失败：取数失败只记日志退出，不发错误推送

## 用法

```bash
python sunset_glow.py --mode digest            # 常规检查（定时任务用）
python sunset_glow.py --mode alert             # 日落前加推检查
python sunset_glow.py --mode test              # 立即发一条真实数据推送

python sunset_glow.py --mode digest --dry-run  # 只打印不发送
python sunset_glow.py --mode digest --force    # 忽略当天去重
```

仅用 Python 标准库，无需安装依赖。日志写入 `logs/sunset_glow.log`。

## 推送效果

标题：`武汉晚霞 21分·平淡`

```
**2026-10-04** ｜ 日落 **18:04** ｜ 天气 阴

**综合评分 21/100 — 平淡**
观赏概率 92%（极易观测）

**能见度** 10.38 km ｜ **置信度** medium
**出动** 建议 17:35 到达，18:34 后可撤
**蓝调** 18:21–18:40（灰蓝有限，30 分）

<sub>数据：glowsunset.cn · Open-Meteo 三模式低云一致</sub>
```

页脚是多模式校验结论：`低云一致`（相差 < 25%）/ `基本一致`（25–40%）/ `⚠️ 分歧大`（≥ 40%）/ `校验缺失`。

## 配置（config.json）

| 字段 | 默认 | 说明 |
|---|---|---|
| `location.spot` | `wuhan` | glowsunset 地点标识，换城市改这里 |
| `location.latitude/longitude` | 武汉 | 交叉校验用的坐标 |
| `thresholds.push_min_quality` | 60 | 推送门槛 |
| `thresholds.advance_fire_quality` | 85 | 提前预告门槛 |
| `schedule.push_window_start/end` | 05:00–21:00 | 允许推送的时段 |
| `schedule.stop_before_sunset_minutes` | 30 | 日落前多久停止推送 |
| `schedule.alert_earliest_hours_before_sunset` | 4.0 | 加推窗口起点（日落前几小时） |
| `schedule.alert_latest_minutes_before_sunset` | 45 | 加推窗口终点（日落前几分钟） |
| `channels.wechat.enabled` | true | 推送开关 |
| `channels.wechat.provider` | pushplus | `pushplus` 或 `serverchan` |
| `advanced.announce_advance_fire_day` | true | 是否预告未来"绝美"日 |
| `advanced.crosscheck_disagreement_spread` | 40 | 判为"分歧"的百分点差 |

## Token

不写在 `config.json` 里。取用顺序：环境变量 `PUSHPLUS_TOKEN`（或 `SERVERCHAN_TOKEN`）→ `local_secrets.json` → `config.json`。

```json
// local_secrets.json
{ "PUSHPLUS_TOKEN": "你的消息token" }
```

PushPlus 用**消息token**（官方定位是给脚本用的，可创建多个、可删除重建），在 https://www.pushplus.plus/ 「一对一推送」页创建。

## 已知数据坑

1. **`probability` 和 `quality` 会背离。** 实测出现过「观赏概率 92% 极易观测」但「评分 21、点评写阴天休战」——概率只管光线通不通，评分才是整体好不好看。脚本只以 `quality` 判定。
2. **细节字段只在"今天"有值。** 云量、能见度、置信度、机位、摄影参数都在接口顶层对象里，只对应当天。未来日期的预告类推送会自动省略这些行。

## 文件

| 文件 | 作用 |
|---|---|
| `sunset_glow.py` | 主脚本 |
| `config.json` | 配置 |
| `local_secrets.json` | token（已 gitignore） |
| `state.json` | 去重状态 |
| `logs/sunset_glow.log` | 运行日志 |
| `run.bat` | 手动运行入口 |
| `register_tasks.ps1` | 恢复本机 Windows 定时任务 |
