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
  if (!resp.ok) throw new Error((await resp.json()).detail ?? "register failed");
  return (await resp.json()).access_token;
}

export async function login(email: string, password: string): Promise<string> {
  const resp = await fetch(`${API_URL}/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok) throw new Error((await resp.json()).detail ?? "login failed");
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
  if (!resp.ok) throw new Error((await resp.json()).detail ?? "research failed");
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
