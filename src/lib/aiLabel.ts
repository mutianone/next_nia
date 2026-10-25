/** Keep connection identities internal; the interface uses one AI brand. */
export const aiLabel = (text: string) => text.replace(/https?:\/\/[^\s]*?(?:siliconflow|deepseek)[^\s]*/gi, 'AI 接口').replace(/硅基流动|deepseek(?:[-/][\w.-]+)?|siliconflow/gi, 'AI');
