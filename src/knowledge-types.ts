export type Retrieval = { mode: string; message: string };
export type KnowledgeStatus = 'draft' | 'published' | 'archived';
export type KnowledgeChunk = { id: string; article: string; text: string; page: number | null; parent_id: string | null; ordinal: number };
export type KnowledgeDocument = {
  id: string; title: string; version: string; authority: string; doc_number: string;
  category: string; source: string; series_key: string; published_on: string | null;
  effective_from: string | null; effective_to: string | null; status: KnowledgeStatus;
  allow_external: boolean; filename: string; file_type: string; size_bytes: number;
  chunk_count: number; created_at: string; updated_at: string; review_notice: string;
  chunks?: KnowledgeChunk[];
};
export type KnowledgeList = { items: KnowledgeDocument[]; total: number; page: number; page_size: number; stats: { documents: number; published: number; drafts: number; chunks: number }; retrieval: Retrieval };
export type Citation = { template_source?: boolean; public_source?: boolean; fetched_at?: string; id: string; document_id: string; title: string; version: string; article: string; text: string; page: number | null; source: string; effective_from: string | null; effective_to: string | null; score: number; allow_external: boolean; source_id?: string };
export type SearchResult = { items: Citation[]; retrieval: Retrieval; as_of: string };
export type Answer = { status: 'answered' | 'insufficient'; answer: string; sources: Citation[]; provider: string | null; model: string | null; as_of: string; retrieval: Retrieval; claims?: { text: string; source_ids: string[] }[] };
export const statusLabels = { draft: '待审核', published: '已发布', archived: '已归档' };
export const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
export const period = (from: string | null, to: string | null) => `${from || '待确认'} — ${to || '未设截止日期'}`;
export const errorMessage = (e: unknown) => e instanceof Error ? e.message : '操作未完成，请重试。';
