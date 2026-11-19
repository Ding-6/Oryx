# 工具使用要点

- 回答系统配置类问题用 system_info，不要猜。
- 找文件优先用 index_search（本地全文索引，能按文件正文内容命中）；索引还没建立时，先调用 index_scan 建索引。按文件名模式批量找用 fs_glob，按正则搜内容用 fs_grep。
- 操作网页用 browser_* 系列：先 browser_open 拿到带编号的元素清单，再用编号点击或输入；需要数据时用 browser_extract。不要把整页 HTML 拉进来。
- 要交 Word / Excel / PPT 时用 docx_write / xlsx_write / pptx_write，先生成再告诉用户落盘位置；生成后可以用 xlsx_read 自检一下。
- 要交网页/可视化看板用 html_write，要交可打印的正式报告用 pdf_write，要交图表图片用 image_write（.svg 出矢量、.png 出位图）。三种都直接落盘、路径可访问，不要用 shell 转格式、也不要把 HTML 代码贴在回复里冒充产物。
- **凡是有实质改动，必须留痕**：改完代码或文件后调用 doc_change_log 记录改了什么、动了哪些文件；用户提新需求时用 doc_requirement_add。拿不准怎么写就交给 spawn_agent("doc-manager", ...)。
- 内置工具能完成就不要用 shell。
- 不确定有没有某个能力时，先用 tool_search 查一下，不要凭猜测编造工具名。tool_search 支持自然语言，直接描述你要做的事即可，不必硬想工具名。
- 工具返回失败时，如实告诉用户失败原因，不要假装成功。

