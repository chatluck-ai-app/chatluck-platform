const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  model?: string | null;
  provider?: string | null;
  created_at?: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ModelInfo {
  id: string;
  provider: string;
  capabilities: string[];
  context_window: number;
  free: boolean;
  available: boolean;
}

/** FastAPI returns detail as a string (our errors) or an array of
 * {loc, msg} objects (request validation). Always produce readable text. */
export function errorDetail(data: unknown, fallback: string): string {
  const d = (data as { detail?: unknown })?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d.length > 0) {
    return d
      .map((e) => {
        const loc = Array.isArray(e?.loc) ? e.loc[e.loc.length - 1] : "";
        const msg = typeof e?.msg === "string" ? e.msg : "";
        return loc ? `${loc}: ${msg}` : msg;
      })
      .filter(Boolean)
      .join(" — ");
  }
  return fallback;
}

/** Map raw provider/HTTP error text to a friendly Arabic message. */
export function friendlyError(raw: string): string {
  const low = raw.toLowerCase();
  if (low.includes("503") || low.includes("high demand") || low.includes("unavailable") || low.includes("overload"))
    return "الخدمة مشغولة شوية دلوقتي بسبب الضغط، جرب تاني بعد ثواني.";
  if (low.includes("429") || low.includes("rate limit") || low.includes("quota"))
    return "وصلنا للحد المؤقت للطلبات، استنى دقيقة وجرب تاني.";
  if (low.includes("no provider") || low.includes("all providers"))
    return "مفيش مزود ذكاء اصطناعي متاح حالياً، جرب بعد شوية.";
  if (low.includes("timeout") || low.includes("timed out"))
    return "المزود استغرق وقت أطول من اللازم، جرب تاني.";
  return "حصل خطأ غير متوقع أثناء تنفيذ طلبك، جرب تاني.";
}

/** True when the error looks transient and worth one automatic retry. */
export function isTransientError(raw: string): boolean {
  const low = raw.toLowerCase();
  return (
    low.includes("503") ||
    low.includes("high demand") ||
    low.includes("unavailable") ||
    low.includes("overload") ||
    low.includes("timeout") ||
    low.includes("مشغولة")
  );
}

export interface ConversationMatch {
  id: string;
  title: string;
  updated_at: string;
  snippet: string;
}

export async function searchConversations(
  token: string,
  q: string
): Promise<ConversationMatch[]> {
  const resp = await fetch(
    `${API_URL}/v1/conversations/search?q=${encodeURIComponent(q)}`,
    { headers: headers(token) }
  );
  if (!resp.ok) throw new Error("فشل البحث في المحادثات");
  return resp.json();
}

function headers(token: string): HeadersInit {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

export async function register(
  email: string,
  password: string,
  displayName: string
): Promise<string> {
  const resp = await fetch(`${API_URL}/v1/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, display_name: displayName }),
  });
  if (!resp.ok)
    throw new Error(errorDetail(await resp.json(), "فشل إنشاء الحساب"));
  return (await resp.json()).access_token;
}

export async function login(email: string, password: string): Promise<string> {
  const resp = await fetch(`${API_URL}/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok)
    throw new Error(errorDetail(await resp.json(), "فشل تسجيل الدخول"));
  return (await resp.json()).access_token;
}

export async function listConversations(token: string): Promise<Conversation[]> {
  const resp = await fetch(`${API_URL}/v1/conversations`, {
    headers: headers(token),
  });
  if (!resp.ok) throw new Error("failed to load conversations");
  return resp.json();
}

export async function listMessages(
  token: string,
  conversationId: string
): Promise<ChatMessage[]> {
  const resp = await fetch(
    `${API_URL}/v1/conversations/${conversationId}/messages`,
    { headers: headers(token) }
  );
  if (!resp.ok) throw new Error("failed to load messages");
  return resp.json();
}

export async function listModels(token: string): Promise<ModelInfo[]> {
  const resp = await fetch(`${API_URL}/v1/models`, { headers: headers(token) });
  if (!resp.ok) throw new Error("failed to load models");
  return resp.json();
}

export interface AttachmentIn {
  mime_type: string;
  data_base64: string;
}

export interface ResearchSource {
  title: string;
  url: string;
  snippet: string;
  source: string;
}

export interface ResearchResponse {
  answer: string;
  provider: string;
  model: string;
  sources: ResearchSource[];
}

export async function research(
  token: string,
  query: string
): Promise<ResearchResponse> {
  const resp = await fetch(`${API_URL}/v1/research`, {
    method: "POST",
    headers: headers(token),
    body: JSON.stringify({ query, language: "ar" }),
  });
  if (!resp.ok)
    throw new Error(errorDetail(await resp.json(), "فشل البحث"));
  return resp.json();
}

export interface StreamHandlers {
  onMeta?: (provider: string, model: string, conversationId: string) => void;
  onToken?: (content: string) => void;
  onFallback?: (from: string, reason: string) => void;
  onError?: (error: string) => void;
  onDone?: (provider: string, model: string) => void;
}

export async function streamChat(
  token: string,
  message: string,
  conversationId: string | null,
  model: string | null,
  h: StreamHandlers,
  attachments: AttachmentIn[] = []
): Promise<void> {
  const resp = await fetch(`${API_URL}/v1/chat`, {
    method: "POST",
    headers: headers(token),
    body: JSON.stringify({
      message,
      conversation_id: conversationId,
      model,
      stream: true,
      attachments: attachments.length > 0 ? attachments : null,
    }),
  });
  if (!resp.ok || !resp.body) {
    const detail = await resp.text();
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "";
  let conversationIdSeen = conversationId;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        const data = JSON.parse(line.slice(5).trim());
        if (eventName === "meta") {
          conversationIdSeen = data.conversation_id;
        } else if (eventName === "token") {
          h.onToken?.(data.content);
        } else if (eventName === "fallback") {
          h.onFallback?.(data.from, data.reason);
        } else if (eventName === "error") {
          h.onError?.(data.error);
        } else if (eventName === "done") {
          h.onDone?.(data.provider, data.model);
        }
      }
    }
  }
  if (conversationIdSeen) {
    h.onMeta?.("", "", conversationIdSeen);
  }
}

export async function deleteConversation(
  token: string,
  id: string
): Promise<void> {
  const resp = await fetch(`${API_URL}/v1/conversations/${id}`, {
    method: "DELETE",
    headers: headers(token),
  });
  if (!resp.ok) throw new Error(errorDetail(await resp.json(), "فشل الحذف"));
}

export async function renameConversation(
  token: string,
  id: string,
  title: string
): Promise<void> {
  const resp = await fetch(`${API_URL}/v1/conversations/${id}`, {
    method: "PATCH",
    headers: headers(token),
    body: JSON.stringify({ title }),
  });
  if (!resp.ok)
    throw new Error(errorDetail(await resp.json(), "فشل إعادة التسمية"));
}

export interface ExtractedFile {
  type: string;
  pages: number | null;
  characters: number;
  text: string;
  truncated: boolean;
}

export async function extractFile(
  token: string,
  file: File
): Promise<ExtractedFile> {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch(`${API_URL}/v1/files/extract`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!resp.ok)
    throw new Error(errorDetail(await resp.json(), "تعذر قراءة الملف"));
  return resp.json();
}

export interface MeInfo {
  id: string;
  email: string;
  display_name: string;
}

export async function getMe(token: string): Promise<MeInfo> {
  const resp = await fetch(`${API_URL}/v1/auth/me`, { headers: headers(token) });
  if (!resp.ok) throw new Error("تعذر تحميل الملف الشخصي");
  return resp.json();
}

export async function updateMe(token: string, displayName: string): Promise<void> {
  const resp = await fetch(`${API_URL}/v1/auth/me`, {
    method: "PATCH",
    headers: headers(token),
    body: JSON.stringify({ display_name: displayName }),
  });
  if (!resp.ok) throw new Error(errorDetail(await resp.json(), "فشل حفظ الاسم"));
}

export async function changePassword(
  token: string,
  currentPassword: string,
  newPassword: string
): Promise<void> {
  const resp = await fetch(`${API_URL}/v1/auth/change-password`, {
    method: "POST",
    headers: headers(token),
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
  if (!resp.ok)
    throw new Error(errorDetail(await resp.json(), "فشل تغيير كلمة السر"));
}

export interface MemoryItem {
  id: string;
  kind: string;
  content: string;
  created_at: string;
}

export async function listMemory(token: string): Promise<MemoryItem[]> {
  const resp = await fetch(`${API_URL}/v1/memory`, { headers: headers(token) });
  if (!resp.ok) throw new Error("تعذر تحميل الذاكرة");
  return resp.json();
}

export async function addMemory(
  token: string,
  kind: string,
  content: string
): Promise<void> {
  const resp = await fetch(`${API_URL}/v1/memory`, {
    method: "POST",
    headers: headers(token),
    body: JSON.stringify({ kind, content }),
  });
  if (!resp.ok) throw new Error(errorDetail(await resp.json(), "فشل حفظ الذاكرة"));
}

export async function deleteMemory(token: string, id: string): Promise<void> {
  const resp = await fetch(`${API_URL}/v1/memory/${id}`, {
    method: "DELETE",
    headers: headers(token),
  });
  if (!resp.ok) throw new Error("فشل حذف الذاكرة");
}

export interface ImageResult {
  url: string;
  provider: string;
  model: string;
}

export interface GalleryImage extends ImageResult {
  id: string;
  prompt: string;
  width: number;
  height: number;
  style: string;
  created_at?: string;
}

export async function imageHistory(token: string): Promise<GalleryImage[]> {
  const resp = await fetch(`${API_URL}/v1/images/history`, {
    headers: headers(token),
  });
  if (!resp.ok) throw new Error("تعذر تحميل المعرض");
  return resp.json();
}

export async function generateImage(
  token: string,
  prompt: string,
  opts: { width?: number; height?: number; style?: string } = {}
): Promise<ImageResult> {
  const resp = await fetch(`${API_URL}/v1/images`, {
    method: "POST",
    headers: headers(token),
    body: JSON.stringify({ prompt, ...opts }),
  });
  if (!resp.ok)
    throw new Error(errorDetail(await resp.json(), "فشل إنشاء الصورة"));
  return resp.json();
}

export interface AgentInfo {
  name: string;
  description: string;
  tools: string[];
  sensitive: boolean;
}

export async function agentCatalog(token: string): Promise<AgentInfo[]> {
  const resp = await fetch(`${API_URL}/v1/agent/catalog`, {
    headers: headers(token),
  });
  if (!resp.ok) throw new Error("فشل تحميل المساعدين");
  return resp.json();
}

export interface AgentStep {
  agent: string;
  instruction: string;
  status: string;
  output: string;
}

export interface AgentRunResult {
  run_id: string;
  status: string;
  steps: AgentStep[];
  blocked: string[];
}

export async function runAgent(
  token: string,
  task: string
): Promise<AgentRunResult> {
  const resp = await fetch(`${API_URL}/v1/agent/run`, {
    method: "POST",
    headers: headers(token),
    body: JSON.stringify({ task, approved: false }),
  });
  if (!resp.ok)
    throw new Error(errorDetail(await resp.json(), "فشل تشغيل المساعد"));
  return resp.json();
}

export async function streamChatAbortable(
  token: string,
  message: string,
  conversationId: string | null,
  model: string | null,
  h: StreamHandlers,
  attachments: AttachmentIn[] = [],
  signal?: AbortSignal,
  opts: { regenerate?: boolean; editMessageId?: string; titleHint?: string } = {}
): Promise<void> {
  const resp = await fetch(`${API_URL}/v1/chat`, {
    method: "POST",
    headers: headers(token),
    body: JSON.stringify({
      message,
      conversation_id: conversationId,
      model,
      stream: true,
      attachments: attachments.length > 0 ? attachments : null,
      regenerate: opts.regenerate || false,
      edit_message_id: opts.editMessageId || null,
      title_hint: opts.titleHint || null,
    }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    const detail = await resp.text();
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "";
  let conversationIdSeen = conversationId;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        const data = JSON.parse(line.slice(5).trim());
        if (eventName === "meta") {
          conversationIdSeen = data.conversation_id;
        } else if (eventName === "token") {
          h.onToken?.(data.content);
        } else if (eventName === "fallback") {
          h.onFallback?.(data.from, data.reason);
        } else if (eventName === "error") {
          h.onError?.(data.error);
        } else if (eventName === "done") {
          h.onDone?.(data.provider, data.model);
        }
      }
    }
  }
  if (conversationIdSeen) {
    h.onMeta?.("", "", conversationIdSeen);
  }
}
