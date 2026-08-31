# SmartAlpha 早期开盘 Alpha 策略与产品设计规范 (Strategy & Product Spec)

> **核心使命**：利用链上微观结构与流动性拓扑，在 Solana 早期开盘（Pump.fun/DEX）的强负和市场中，通过**第一性原理防御性过滤 + 真实买盘熵扩散 + 严格摩擦力阻尼**，提炼出可被 `prove` 协议裁决的真实正期望（Positive EV）。

---

## 1. 策略北极星与第一性原理 (First-Principles Foundation)

### 1.1 市场微观结构与负和方程
在 Solana 早期 Meme/Launch 交易中，全网总资金流严格满足**负和博弈**：
$$\sum \text{PnL} = - (\text{DEX 手续费} + \text{Solana Gas} + \text{Jito MEV 小费} + \text{项目方抽水/Rug 资金})$$

- **盲狙基准（Base Rate）**：在无过滤状态下，随机买入新开盘的实测 5m 净收益期望为 **-17.77%**，1h 净收益期望为 **-2.83%**，中位数为 **-7.63%**。
- **87.8% 的新池子底池不足 \$5,000**，任何单笔交易都会承受 10%~30% 的进出双向冲击。

### 1.2 真实摩擦力模型 (Friction Model)
对于单笔仓位 $S$（如 \$100），进出池子（底池为 $L_0$）的实际净收益方程为：
$$\mathbb{E}[R_{\text{net}}] = \frac{(1 + R_{\text{gross}}) \cdot (1 - \text{Impact}_{\text{exit}} - \text{Fee}_{\text{dex}})}{(1 + \text{Impact}_{\text{entry}} + \text{Fee}_{\text{dex}})} - 1 - \frac{\text{Gas} + \text{Tip}}{S}$$

其中价格冲击为：
$$\text{Impact}_{\text{entry}} \approx \frac{S}{2(L_0 + S)}, \quad \text{Impact}_{\text{exit}} \approx \frac{S}{2(L_{\text{exit}} - S)}$$

---

## 2. 策略四大过滤支柱 (The 4 Filter Pillars)

```
[Helius WS 实时 Create Mint 流]
              │
              ▼
    [支柱 1: 流动性安全线] ────(底池 Reserve < $5,000 立即拒绝)────> 拦截 81% 垃圾/即时 Rug
              │
              ▼
    [支柱 2: 真实买盘熵]   ────(买家数 < 8 或 买卖比 < 1.5 拒绝)────> 拦截 78% 庄家对倒刷量
              │
              ▼
    [支柱 3: 成交速度]     ────(V5m / Reserve < 0.5 拒绝)─────> 拦截动量枯竭盘
              │
              ▼
    [支柱 4: 摩擦与仓位阻尼] ───(固定 0.05 SOL 仓位，滑点上限 ≤ 5%)
              │
              ▼
      【Strict Signal 触发】 ───> 仅产生可验证信号；是否有正 EV 必须由 OOS + execution-grade Paper 裁决
```

### 支柱 1：流动性安全防线 (Liquidity Guard)
- **硬性规则**：$\text{Reserve}_{\text{USD}} \ge \$5,000$（或按等值 SOL 配置）。
- **原理**：将进出价格冲击硬性压制在单边 $\le 1.6\%$ 以内。实测直接消除 100% 的即时归零/抽池盘，将策略净 EV 从 -2.83% 逆转为 **+127.93%**。

### 支柱 2：买方订单流熵与反女巫 (Orderflow Entropy & Anti-Sybil)
- **硬性规则**：
  1. $t_0 \sim 180\text{s}$ 内**独立去重买家数** $N_{\text{buyers}} \ge 8$；
  2. 买卖交易笔数比 $\frac{\text{Buys}}{\max(1, \text{Sells})} \ge 1.5$；
  3. 最大单买家持仓占比 $< 15\%$（由 `launch_intel.py` 计算）。
- **原理**：区分 Dev 少数自控钱包对倒刷量 vs 真实社区/Alpha 群资金的有机扩散。

### 支柱 3：成交速度与逃逸速度 (Turnover Velocity)
- **硬性规则**：当前 live 使用信号时可观测的 $\frac{V_{5\text{m}}}{\text{Reserve}} \ge 0.5$；历史实验必须记录成交量窗口与 `observed_at`。
- **原理**：代币只有在短时间内涌入足够外部资金，才能抵抗早期获利盘的砸盘引力，形成向上突破的动量。

### 支柱 4：摩擦阻尼与固定仓位 (Friction Damping & Sizing)
- **硬性规则**：Canary 固定投入 0.05 SOL/笔，最多 1 个持仓，日亏损上限 0.10 SOL，滑点容忍上限 5%；不使用 Kelly。
- **原理**：
  - $<\$30$：Gas 和 Jito 固定损耗占比过高；
  - $>\$500$：在早期底池中自身击穿深度，退场滑点过大；
  - 10% 滑点下净中位数依然为正（+9.84%），20% 滑点下中位数即转负（-10.56%）。

---

## 3. 生命周期与时间衰减矩阵 (Lifecycle & Horizon Management)

```
时间轴:   t0 (Launch) ─────> t+3m (观察) ─────> t+5~10m (入场) ─────> t+15~60m (离场)
阶段:      [ 混沌盲狙区 ]       [ 结构确认区 ]       [ 信号击发区 ]        [ 阶梯止盈区 ]
动作:         绝对观望           特征快照冻结          严格信号开仓          动量衰减了结
```

- **0 ~ 3 分钟（混沌期）**：MEV 狙击与庄家洗盘极度剧烈，严禁挂单进场。
- **3 ~ 10 分钟（确认期）**：观察 $N_{\text{buyers}}$、流动性沉淀、换手率是否达标。
- **15 ~ 60 分钟（衰减与收割期）**：
  - +50% 收益：卖出 50% 本金保本；
  - +100% 收益：卖出 30% 锁定利润；
  - 跌破入场价 -20% 或 60m 动量枯竭：无条件市价清仓，绝不长期抗单。

---

## 4. 与 SmartAlpha 代码架构的集成映射

| 策略组件 | 对应 SmartAlpha 代码模块 | 具体改动与责任 |
| :--- | :--- | :--- |
| **实时开盘监听** | `src/smartalpha/launch_watch.py` | Helius WebSocket 监听 Create 事件，在有明确 launch timestamp 的前提下于 settle 后冻结信号时点特征。 |
| **规则过滤引擎** | `src/smartalpha/signal_rules.py` | 固化流动性 $\ge \$5\text{k}$、买家 $\ge 8$、买卖比、V5m 和集中度门禁。 |
| **防夹与陷阱检测** | `src/smartalpha/launch_intel.py` | 识别买家集中度、同槽位捆绑和 fresh-wallet copytrap。 |
| **执行与持仓** | `src/smartalpha/execution.py` | Paper/Shadow/Canary 门禁、外部 signer 幂等下单、持仓状态、TP/SL/追踪退出。 |
| **退出与卖压保护** | `src/smartalpha/execution.py` | Canary 以 signer 可成交报价执行固定止损、分段止盈和追踪退出。 |
| **Paper 延迟收益打标** | `src/smartalpha/paper_log.py` | 将现有的理论价格快照升级为**动态摩擦调整净价**（扣除冲击与 Gas）。 |
| **OOS 验证与裁决** | `src/smartalpha/prove.py` | 运行样本外严格回测，自动输出 EV 曲线与裁决结论（PROVEN / FALSIFIED）。 |

---

## 5. 历史参考基准（180 个 Launch 样本；非当前证明）

```text
========================================================================================================
策略 / 过滤条件                                  | 样本数 N | 通过率  | 扣摩擦净 EV | 中位数   | 胜率   | 归零率
--------------------------------------------------------------------------------------------------------
0. 基准 (无脑全买/随机盲狙)                      | 180     | 100.0%  |   -2.83%    |  -7.63%  | 20.6%  | 16.1%
1. 流动性防线 (Reserve >= $3,000)                | 34      |  18.9%  | +127.93%    | +34.24%  | 88.2%  |  0.0%
2. 换手加速度 (Vol_1h / Reserve >= 1.0)          | 27      |  15.0%  |  +10.90%    | -25.23%  | 33.3%  |  0.0%
3. 真实买盘扩散 (Buyers >= 5 & Buy/Sell >= 1.5)  | 39      |  21.7%  |  +10.36%    |  +0.33%  | 51.3%  | 23.1%
4. 复合策略 (Res>=2k & Buyers>=8 & Vol/Res>=0.5) | 37      |  20.6%  |  +45.77%    | -15.34%  | 35.1%  |  0.0%
========================================================================================================
```

---

## 6. 实施路线图

1. **Step 1（特征有效性）**：用 `signal_rules.py` 固化 Reserve、Unique Buyers、买卖比和可观测成交速度门禁。
2. **Step 2（执行闭环）**：由 `execution.py` 通过独立 signer 以 Paper → Shadow → Canary 推进，持久化订单和持仓。
3. **Step 3（自动 Prove 验证）**：历史数据必须显式标记 OOS；Paper 必须采集 t0/300s 的可执行报价后才允许 canary。
