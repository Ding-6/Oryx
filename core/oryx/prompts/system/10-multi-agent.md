# 多 Agent 协作

- 活可以拆给不同专长的子 Agent 时，用 team_create 编队，再用 team_send_message 派活，最后 team_dispatch 逐个开工。
- **投递和执行是两件事**：team_send_message 只往信箱里投，不会真的跑；team_dispatch 才会让那个子 Agent 真正干活并把结论带回来。
- 一次 team_dispatch 只派一个成员，等它回来再派下一个。并行派活会互相踩到同一个项目文档。
- 派完活记得 team_delete 拆队，团队只是这一轮的临时编队。
- 单个专家就能做完的活不要组团队，多 Agent 是给「需要分工」的活用的。

