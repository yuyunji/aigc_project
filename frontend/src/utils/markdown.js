/**
 * 简单 Markdown → HTML 渲染器
 * 处理 Claude 返回的 Markdown 格式：标题、加粗、列表、分隔线、段落
 * 不引入第三方库，代码清晰易读，适合面试展示
 */

export function renderMarkdown(text) {
  if (!text) return "";

  // 1. 转义 HTML 特殊字符，防止 XSS
  let html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // 2. 代码块 ```...```（在其它规则之前处理，防止内部被转义）
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    return `<pre><code>${code.trim()}</code></pre>`;
  });

  // 3. 行内代码 `...`
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

  // 4. 标题 ## ... 和 ### ...
  html = html.replace(/^#### (.+)$/gm, "<h4>$1</h4>");
  html = html.replace(/^### (.+)$/gm, "<h4>$1</h4>");
  html = html.replace(/^## (.+)$/gm, "<h3>$1</h3>");

  // 5. 加粗 **text**
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  // 6. 无序列表 - item
  html = html.replace(/^- (.+)$/gm, "<li>$1</li>");
  // 将连续 <li> 包裹在 <ul> 中
  html = html.replace(/(<li>.*?<\/li>\n?)+/g, "<ul>$&</ul>");

  // 7. 有序列表 1. item
  html = html.replace(/^\d+\. (.+)$/gm, "<li>$1</li>");

  // 8. 分隔线 --- 或 ***
  html = html.replace(/^(---+|\*\*\*+)$/gm, "<hr>");

  // 9. 引用 >
  html = html.replace(/^&gt; (.+)$/gm, "<blockquote>$1</blockquote>");

  // 10. 段落：连续非空行
  html = html.replace(/\n\n+/g, "</p><p>");
  html = "<p>" + html + "</p>";

  // 11. 清理空标签
  html = html.replace(/<p>\s*<\/p>/g, "");
  html = html.replace(/<p>(<h[34]>)/g, "$1");
  html = html.replace(/(<\/h[34]>)<\/p>/g, "$1");
  html = html.replace(/<p>(<ul>)/g, "$1");
  html = html.replace(/(<\/ul>)<\/p>/g, "$1");
  html = html.replace(/<p>(<blockquote>)/g, "$1");
  html = html.replace(/(<\/blockquote>)<\/p>/g, "$1");
  html = html.replace(/<p>(<pre>)/g, "$1");
  html = html.replace(/(<\/pre>)<\/p>/g, "$1");
  html = html.replace(/<p>(<hr>)<\/p>/g, "$1");

  // 12. 单换行 → <br>（保留诗歌/台词换行）
  html = html.replace(/\n/g, "<br>");

  return html;
}
