# 场站实测功率数据调研（2026-09-15）

本文补充 [发电预测校准数据调研](./calibration-data-survey-2026-09-15.md) 的「二、场站级数据」，这次专找**场站功率或电量的实测时间序列**。
检索范围分三块：

- 国内的竞赛与科研数据平台；
- 国际开源仓库：GitHub、Kaggle、Zenodo、figshare、Dryad、IEEE DataPort、Hugging Face 等；
- 各国电网与统计机构的官方开放数据。

说明：

- 只打开了数据说明页、README、论文、数据字典和许可条款，**没有下载数据，也没有注册账号**。
- 「已核实」指打开过官方页面或数据仓库元数据；「未核实」指只看到检索摘要或参赛者博客。
- 2022 年以后的数据还能用 Open-Meteo 历史预报检验预报精度；更早的数据只能用 ERA5 检验物理链路。

## 结论

1. **中国境内带精确坐标、能公开拿到的场站实测只有两个光伏数据集**：
   - PVOD：河北 10 座地面光伏，2018–2019；
   - 香港科大：60 座屋顶光伏，2021–2023，CC0。
2. **中国风电场没有带坐标的公开实测。** 国网竞赛数据（CC0）有 6 座风电场的容量、轮毂高度和测风塔实测，没有坐标。不过它只用来检验「轮毂风速 → 功率」这一段，不需要坐标。
3. **规模化验证只能靠国外官方数据。** 最好的两个都带限电信息，能把限电从模型偏差里剔掉：
   - 澳大利亚 AEMO：逐机组 5 分钟，2009 年至今，有无约束预测和可用容量；
   - 巴西 ONS 限电数据集：逐电站半小时，给实发、参考发电、可用容量和限电原因，CC-BY。

   两者的电站坐标都可以按名称关联 GEM 全球追踪表，就是我们目录在用的数据源。
4. **海上风电在开源仓库里是空白。** 官方来源有两个：
   - 台电：台湾海峡约 16 个海上风电条目，10 分钟，可商用；
   - ENTSO-E：欧洲 ≥100 MW 机组，CC BY 4.0。
5. **有几个国内线索可能规模更大，要人工登录才能确认**：宁夏 20 座风电加 29 座光伏、2024 数字中国创新大赛光伏与海上风电赛题等，见第二节。
6. **没有限电或停机标记的数据，偏差要谨慎解读。** 实测低于模型的部分，分不清是模型高估还是限电和停机。

## 一、推荐清单

### A. 中国场站

| # | 数据集 | 地点与规模 | 坐标 | 分辨率与时段 | 附带字段 | 许可 | 获取 | 能检验什么 |
|---|---|---|---|---|---|---|---|---|
| 1 | **PVOD v1.0**<br>[ScienceDB 10.11922/sciencedb.01094](http://www.doi.org/10.11922/sciencedb.01094) · [GitHub](https://github.com/yaotc/PVODataset) | 河北 10 座地面光伏，6.6–35 MW，含组件技术、倾角 | 精确，逐站经纬度 | 15 分钟，约 2018-07 至 2019-06 | NWP 7 项；站点实测总辐照、散射辐照、气温、气压、风 | README 写明限科研、须引用；ScienceDB 页许可未核实 | ScienceDB 下载 | 用实测辐照直接驱动 PVWatts，把「辐照 → 功率」的转换误差（容配比、系统损耗、温度）单独拆出来；再换成 ERA5 看全链路 |
| 2 | **国网新能源发电功率预测竞赛**<br>[figshare 17304221](https://figshare.com/articles/dataset/Solar_and_wind_power_data_from_the_Chinese_State_Grid_Renewable_Energy_Generation_Forecasting_Competition/17304221) · [Sci Data 2022](https://www.nature.com/articles/s41597-022-01696-6) | 6 座风电场 36–200 MW，轮毂 65–120 m；8 座光伏 30–130 MW；分布在华北、华中、西北 | 无。第三方仓库 [Zhaohh0706/pv-wind-power-forecast](https://github.com/Zhaohh0706/pv-wind-power-forecast) 用正午时刻、昼长、气压反推过 | 15 分钟，2019–2020 | 测风塔多层风速风向、气温气压湿度；光伏辐照（3 个站辐照不可用） | CC0 | 直接下载，原始包 78 MB | 实测轮毂风速 → 机型功率曲线 → 空气密度修正 → 10% 损耗，不需要坐标 |
| 3 | **香港科大 60 座屋顶光伏**<br>[Dryad 10.5061/dryad.m37pvmd99](https://datadryad.org/dataset/doi:10.5061/dryad.m37pvmd99) · [Sci Data 2025](https://www.nature.com/articles/s41597-025-04397-y) | 60 座并网屋顶光伏，同一校园 | 园区级，22.3363°N、114.2634°E | 功率 5 分钟，2021–2023 | 园区气象塔 1 分钟；Brick 元数据（逐站容量与朝向未核实） | CC0 | 直接下载，296 MB | 湿热多云气候下，用 Open-Meteo 历史预报检验光伏预测精度。局限：屋顶分布式、朝向各异，60 座站都落在同一个气象格点里 |

### B. 国外大样本

| # | 数据集 | 地点与规模 | 坐标 | 分辨率与时段 | 限电 / 停机信息 | 许可 | 获取 | 能检验什么 |
|---|---|---|---|---|---|---|---|---|
| 4 | **AEMO NEMWEB**<br>[当期目录](https://nemweb.com.au/Reports/Current/) · [历史归档](https://nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM/) | 澳大利亚东部电网，全部风电与大型光伏 | 按名称关联 GEM | 5 分钟，2009 年至今，持续更新 | `DISPATCHLOAD`（次日公开）有 UIGF 无约束预测、AVAILABILITY、TOTALCLEARED；`INTERMITTENT_GEN_SCADA` 有可用风机 / 逆变器数和本地限值 | 检索摘要称任意用途、须署名，**原文未核实** | 免注册直接下载，按月打包 | 全链路和 7 天预报精度；按「无约束预测 − 出清值」剔除限电。内陆干旱、大温差，接近西北 |
| 5 | **ONS 限电数据集**<br>[风电](https://dados.ons.org.br/dataset/restricao_coff_eolica_usi) · [光伏](https://dados.ons.org.br/dataset/restricao_coff_fotovoltaica_detail) | 巴西调度级风电场与光伏电站 | 电站编码关联电站名录（名录坐标字段未核实）或 GEM | 半小时；风电 2021-12 起，光伏 2024-05 起；每天更新 | 实发、受限后发电、可用容量、参考发电（不限电时应发）、限电原因 | CC-BY | 直接下载 CSV / Parquet | 同上。东北部信风区风电集群规模大。参考发电是 ONS 模型值，用它比精度前要先弄清算法 |
| 6 | **台电各机组发电量**<br>[即时](https://data.gov.tw/dataset/8931) · [历史](https://data.gov.tw/dataset/37331) | 台湾海上风电约 16 条、陆上风电部分逐场、大型光伏约 16 座；其余约 15 GW 光伏合成一条 | 按名称关联 GEM | 10 分钟瞬时值；历史滞后约 3 个月 | 只有备注栏（计划停机、检修、故障、测试） | 政府资料开放授权 1.0，可商用、须署名 | 直接下载 JSON | 海上风电链路（海上格点、轮毂插值、密度修正），和福建、浙江近海同属一个气候 |
| 7 | **Kelmarsh / Penmanshiel / Hill of Towie**<br>[Zenodo 16807551](https://zenodo.org/records/16807551) · [16807304](https://zenodo.org/records/16807304) · [14870023](https://zenodo.org/records/14870023) | 英国 3 个风电场，共 41 台 2 MW 级风机 | 逐台精确，含轮毂高度 | 10 分钟，2016 至 2024 年 | 逐台 SCADA、事件日志、关口电表；Hill of Towie 有每 10 分钟停机秒数（时间戳标在区间末） | CC BY 4.0 | 直接下载，3.3 / 6.8 / 12.7 GB | 风电全链路与 2022 年后的预报精度；按停机剔除可利用率损失 |
| 8 | **ENTSO-E 16.1.A 逐机组实际发电**<br>[说明](https://transparencyplatform.zendesk.com/hc/en-us/articles/16648326220564) | 欧洲 ≥100 MW 机组，以北海海上风电为主 | EIC 码关联 GEM 或各国登记表 | 1 小时或 15 分钟，2015 年至今 | 无限电标记，停运要另查 | CC BY 4.0 | 免费注册，邮件申请 token | 海上风电大样本 |

### C. 专项补充

| 数据集 | 内容 | 许可与获取 | 专门用来检验 |
|---|---|---|---|
| [DKASC Alice Springs](https://dkasolarcentre.com.au/download?location=alice-springs) | 澳大利亚沙漠光伏阵列，固定、单轴、双轴跟踪，5 分钟，2008 年至今 | 免注册；原始数据限制再分发，商用条款未写明 | 跟踪支架分支、组件温度模型、大温差 |
| [NREL PVDAQ](https://data.openei.org/submissions/4568) | 美国光伏系统，含 257 MW 与 4.7 MW（科罗拉多高海拔）单轴跟踪电站；经纬度、倾角、逆变器齐全 | S3 免账号；页面写 CC BY 4.0，文档另提 BSD-3，两处不一致 | 容配比与交流限幅、跟踪支架 |
| [EIA-923](https://www.eia.gov/electricity/data/eia923/) + [EIA-860](https://www.eia.gov/electricity/data/eia860/) | 美国逐电站**月度**净发电量；官方坐标、轮毂高度、跟踪方式、倾角方位 | 直接下载 | 月电量偏差按气候区校准。不能检验曲线形状，也没有限电标记 |
| [巴西 Pedra do Sal / Beberibe](https://zenodo.org/records/1475197) | 两座沿海风电场逐台 SCADA，100 m 测风塔 5 层风速，2013–2014 | CC BY-SA 4.0，直接下载 | 按层插值与风切变 |
| [丹麦能源署风机登记表](https://ens.dk/analyser-og-statistik/data-oversigt-over-energisektoren) | 逐台风机坐标、机型、轮毂高度、月度电量，含海上风电 | 直接下载；没有明确许可条款 | 机型分档与海上风电的月电量 |
| Elexon B1610 + BOALF + [REPD](https://www.gov.uk/government/publications/renewable-energy-planning-database-monthly-extract) | 英国逐机组半小时电量与减出力指令；REPD 名录给坐标 | API 免 key；REPD 为 OGL v3；B1610 许可只看到摘要 | 剔除限电后的海上风电 |

## 二、要人工登录才能确认的国内线索

| 线索 | 已知信息 | 要确认的事项 |
|---|---|---|
| [宁夏 20 个风电场 + 29 个光伏电站历史功率与 NWP](https://www.nbsdc.cn/general/dataDetail?id=63a012db1f97ac1dc8082b2a&type=1)（国家基础学科公共科学数据中心） | 只看到标题，详情页要注册登录 | 坐标、容量、时段、使用协议。**若可用，就是规模最大的中国多站数据** |
| [2024 数字中国创新大赛 · 光伏发电出力预测](https://www.datafountain.cn/datasets/7021) | 据博客：分布式光伏，15 分钟，2022–2023，基本信息有经纬度与装机 | 坐标是否脱敏；下载协议是否允许赛事外做内部验证 |
| [2024 数字中国创新大赛 · 海上风电出力预测](https://www.datafountain.cn/competitions/1008) | 据博客：5 座海上风电场，15 分钟，2022-01 至 2024-01，有位置和容量 | 同上，另外确认赛后还能不能下载。**若可用，就是唯一的中国海上风电实测** |
| IEEE DataPort 张北风电场 12 台机组 | 1.5 MW 机组，60 秒，2014-03 至 2015-03（只看到列表页简介） | 能否免费获取、有无逐台坐标。坝上高原，气候最对口 |
| [2021 国家电网调控人工智能创新大赛](https://aistudio.baidu.com/aistudio/competition/detail/110) | 新闻稿称含风电场出力、机组数据和 AGC 限值 | 赛后是否公开 |
| [景云天气能源气象科研数据平台](https://energymeteo-pseo.sdu.edu.cn/dataset)（山东大学） | 前端渲染，抓不到正文 | 数据集清单与获取方式 |

以下赛题价值有限：第三届世界科学智能大赛新能源赛道、讯飞新能源发电功率预测挑战赛。都是 2024 年起的 5 风 5 光，但功率做了归一化，也没有坐标，量级误差检验不了。

## 三、看过但不能用

- **合成或模拟数据**：
  - Time-Series-Library / LSTNet 的「Solar」，来源是 NREL Solar Power Data for Integration Studies；
  - NREL WIND Toolkit；
  - PSML；
  - 北部湾 10 年理论出力。
- **没有位置或来源不明**：Kaggle「Wind Power Generation Data – 4 locations」、Kaggle 印度 2 座电站、Texas A&M 风电数据集、CARE to Compare、EDP Wind Farm 1。
- **要订阅且没有坐标和容量**：IEEE DataPort 的内蒙古西部风光、华南风光。
- **访问或许可受限**：
  - Ørsted 海上风电：签 NDA，限非商业科研；
  - Open Electricity 电站坐标：CC BY-NC；
  - 土耳其 EPİAŞ：2024-08 起要登录，许可不明。
- **只有汇总，不能逐站用**：EIA-930、Energinet、韩国 KPX、南非 Eskom、墨西哥 CENACE。印度、越南没找到逐站公开数据。
- **只有位置、没有功率**：CPVPD-2024、ChinaPV、10 m 光伏电站图斑。它们可以用来核对目录坐标。

## 四、怎么用

分两条线，分别回答不同的问题。

1. **物理转换误差**：用场站实测气象驱动模型，把气象预报误差排除掉。
   - PVOD 实测总辐照与散射辐照 → `metrics/pv.hourly_power` → 对比实测功率；
   - 国网竞赛测风塔轮毂高度风速 → 机型功率曲线 → 对比实测功率；
   - 巴西 UEPS 多层测风 → 检验按层插值。

   这条线校准的是：容配比、系统损耗、温度模型、风电机型档和 10% 损耗。
2. **全链路与预报精度**：用 ERA5 和 Open-Meteo 历史预报驱动，与实测功率对比。
   - 用 AEMO、ONS、台电、英国三个风电场、香港科大；
   - 2022 年以后的时段才能检验预报精度。

注意事项：

- **限电**：有无约束预测、可用容量或停机记录的数据（AEMO、ONS、Hill of Towie、Elexon）先剔除受限时段再算偏差；没有这些字段的，只能取不限电的地区和时段。
- **时间口径**：
  - AEMO 的 `SCADAVALUE` 是时段起点瞬时值，台电是 10 分钟瞬时值；
  - Hill of Towie 标在区间末；
  - 对齐方法按 07 §2.1、§2.7。
- **容量口径**：
  - 官方数据多为净发电量，已扣厂用电；
  - AEMO 的机组编号、ENTSO-E 的 EIC 码与电站不一一对应，分期或拆分的要先合并，再和 GEM 的分期容量对上。
- **许可**：PVOD 限科研，DKASC 限再分发，Open Electricity 不可商用。内部校准与对外展示分开考虑，接入前再核一次原文。
- 校准结论写进 07 §8.1 与 `docs/reports/`。改了模型参数或分档断点，要重跑 `scripts/calibrate.py`。

## 五、建议顺序

1. **第一批，中国场站，数据量小**：
   - 国网竞赛：CC0，78 MB，检验风电功率曲线段；
   - PVOD：检验光伏转换段；
   - 香港科大：296 MB，检验 2022 年后的光伏预报。
2. **第二批，大样本与预报精度**：
   - AEMO：2023–2025 年若干个月的逐机组 SCADA 与 `DISPATCHLOAD`；
   - 台电：海上风电历史；
   - ONS：风电限电数据集。
3. **同时**：人工登录确认宁夏数据集和两个数字中国创新大赛赛题，能用的并入第一批。
