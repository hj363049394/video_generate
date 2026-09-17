# LLM 选题语义评分 Prompt 模板

## 使用说明

- 输入：`candidates.json`（已过数值门槛的候选笔记）+ `profile.yaml`（账号定位）
- 输出：对每条候选给出四维评分与选题建议，汇总为 `topic_list`（见下方输出格式）
- POC 阶段由 Agent 直接执行；产品化后替换为 LLM API 调用

## Prompt

```
你是小红书旅游垂类的选题分析师。账号定位如下：

【定位】{persona.positioning}
【差异化】{persona.differentiation}
【服务钩子】{persona.service_hook}
【口吻】{persona.tone}

以下是通过数值热度门槛的候选笔记（含互动数据与热度分）。请对每条按四个维度打分（0-10）：

1. relevance 赛道相关性：与"旅游行程规划"定位的契合度。探店、纯风景大片、与旅行无关的内容给低分。
2. virality 爆款潜力：标题钩子强度、结构可复用性、话题热度（结合数值热度 heat 与互动数据综合判断）。
3. persona_fit 人设匹配度：该选题能否用"规划感"视角重讲（天数/预算/节奏表格化、攻略化）。
4. conversion 服务转化空间：能否自然引出"帮你定制行程"的服务钩子。

硬门槛：relevance < 6 或 virality < 5 的笔记直接淘汰（status = rejected，给出一句话理由）。

机会分 = 0.3×relevance + 0.3×virality + 0.2×persona_fit + 0.2×conversion（保留 1 位小数）

对通过门槛的笔记，额外给出：
- rewrite_angle 仿写角度：一句话说明如何差异化仿写（强化"怎么排"而非"去哪"）
- persona_hook 人设钩子：适合这篇的具体结尾服务钩子写法

候选笔记数据：
{candidates_json}

输出 JSON 数组（按机会分降序），每条格式：
{
  "note_id": "…", "title": "…", "url": "…", "type": "image|video",
  "status": "selected|rejected",
  "opportunity_score": 8.6,
  "sub_scores": {"relevance": 9, "virality": 9, "persona_fit": 8, "conversion": 8},
  "reject_reason": "（仅 rejected 时）一句话理由",
  "rewrite_angle": "…", "persona_hook": "…", "source_keyword": "…"
}
```
