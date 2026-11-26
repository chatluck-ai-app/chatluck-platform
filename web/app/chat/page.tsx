"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AgentInfo,
  AttachmentIn,
  ChatMessage,
  Conversation,
  ConversationMatch,
  ModelInfo,
  ResearchSource,
  agentCatalog,
  MeInfo,
  MemoryItem,
  addMemory,
  changePassword,
  deleteConversation,
  deleteMemory,
  extractFile,
  friendlyError,
  getMe,
  generateImage,
  isTransientError,
  listConversations,
  listMessages,
  listModels,
  listMemory,
  GalleryImage,
  imageHistory,
  renameConversation,
  research,
  runAgent,
  searchConversations,
  streamChatAbortable,
  updateMe,
} from "../lib/api";

type Mode = "chat" | "research" | "image" | "translate" | "code";

interface UiMessage extends ChatMessage {
  imageUrl?: string;
  sources?: ResearchSource[];
  streaming?: boolean;
}

const TOOLS: { mode: Mode | "agents"; icon: string; name: string; desc: string }[] = [
  { mode: "research", icon: "🔎", name: "بحث الإنترنت", desc: "إجابات بمصادر حقيقية وروابط موثوقة" },
  { mode: "image", icon: "🎨", name: "استوديو الصور", desc: "أنشئ صوراً من وصف نصي بالعربي أو الإنجليزي" },
  { mode: "translate", icon: "🌍", name: "الترجمة الاحترافية", desc: "ترجمة بسياق وبأسلوب رسمي أو طبيعي" },
  { mode: "code", icon: "💻", name: "مساعد البرمجة", desc: "اكتب واشرح وأصلح الكود بأي لغة" },
  { mode: "agents", icon: "🤖", name: "المساعدون المتخصصون", desc: "فريق وكلاء ينفذ مهام مركبة خطوة بخطوة" },
];

const SUGGESTIONS = [
  "اشرحلي الذكاء الاصطناعي ببساطة",
  "اكتبلي خطة تعلم برمجة في ٣ شهور",
  "لخصلي أهم أخبار التكنولوجيا",
  "ساعدني أكتب إيميل رسمي بالإنجليزي",
  "إيه الفرق بين بايثون وجافاسكريبت؟",
  "اقترحلي أفكار مشروع صغير",
];

/** Collapse a stored user message that embeds an analyzed document to a
 * short readable form: the user's request plus a file badge. */
function displayUserContent(c: string): string {
  const marker = "طلبي بخصوص الملف: ";
  if (c.includes("<document>") && c.includes(marker)) {
    const q = c.split(marker).pop() || c;
    const name = c.match(/الملف المرفق "([^"]+)"/)?.[1];
    return `${q}\n📄 ${name ?? "ملف مرفق"}`;
  }
  return c;
}

const MODE_META: Record<Mode, { chip: string; placeholder: string }> = {
  chat: { chip: "", placeholder: "اكتب رسالتك…" },
  research: { chip: "🔎 وضع البحث", placeholder: "اكتب سؤالك وهبحث في الإنترنت وأجيبك بالمصادر…" },
  image: { chip: "🎨 وضع الصور", placeholder: "اوصف الصورة اللي عايزها…" },
  translate: { chip: "🌍 وضع الترجمة", placeholder: "اكتب النص واللغة المطلوبة…" },
  code: { chip: "💻 وضع البرمجة", placeholder: "اشرح المطلوب أو الصق الكود…" },
};

export default function ChatPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConv, setActiveConv] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState<string>("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [mode, setMode] = useState<Mode>("chat");
  const [attachments, setAttachments] = useState<
    { name: string; mime_type: string; data_base64: string }[]
  >([]);
  const [docAttachment, setDocAttachment] = useState<
    | { name: string; type: string; characters: number; truncated: boolean; text: string }
    | null
  >(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentTask, setAgentTask] = useState("");
  const [agentBusy, setAgentBusy] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [searchQ, setSearchQ] = useState("");
  const [searchResults, setSearchResults] = useState<ConversationMatch[] | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [me, setMe] = useState<MeInfo | null>(null);
  const [meName, setMeName] = useState("");
  const [pwCurrent, setPwCurrent] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [settingsMsg, setSettingsMsg] = useState("");
  const [memory, setMemory] = useState<MemoryItem[]>([]);
  const [memKind, setMemKind] = useState("preference");
  const [memContent, setMemContent] = useState("");
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [imgSize, setImgSize] = useState("1024x1024");
  const [imgStyle, setImgStyle] = useState("");
  const [galleryOpen, setGalleryOpen] = useState(false);
  const [gallery, setGallery] = useState<GalleryImage[]>([]);
  const [listening, setListening] = useState(false);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const recognitionRef = useRef<{ stop: () => void } | null>(null);
  const dictationBaseRef = useRef("");
  const [speechSupported, setSpeechSupported] = useState(false);

  useEffect(() => {
    const w = window as unknown as Record<string, unknown>;
    setSpeechSupported(Boolean(w.SpeechRecognition || w.webkitSpeechRecognition));
  }, []);

  function toggleDictation() {
    if (listening) {
      recognitionRef.current?.stop();
      setListening(false);
      return;
    }
    const w = window as unknown as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    const SR = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!SR) return;
    dictationBaseRef.current = input;
    const rec = new SR();
    rec.lang = "ar-EG";
    rec.interimResults = true;
    rec.continuous = false;
    rec.onresult = (e: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => {
      let text = "";
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
      setInput(dictationBaseRef.current + text);
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recognitionRef.current = rec;
    rec.start();
    setListening(true);
  }

  function stripMarkdown(c: string): string {
    return c
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/[#*_`>|\-[\]]/g, "")
      .replace(/\((https?:[^)]+)\)/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function toggleSpeak(m: UiMessage) {
    const synth = window.speechSynthesis;
    if (!synth) return;
    if (speakingId === m.id) {
      synth.cancel();
      setSpeakingId(null);
      return;
    }
    synth.cancel();
    const u = new SpeechSynthesisUtterance(stripMarkdown(m.content).slice(0, 4000));
    u.lang = "ar-SA";
    u.rate = 1;
    u.onend = () => setSpeakingId(null);
    u.onerror = () => setSpeakingId(null);
    synth.speak(u);
    setSpeakingId(m.id);
  }

  useEffect(() => {
    const saved = (localStorage.getItem("chatluck_theme") as "dark" | "light") || "dark";
    setTheme(saved);
    document.documentElement.dataset.theme = saved;
  }, []);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    localStorage.setItem("chatluck_theme", next);
  }

  async function openSettings() {
    if (!token) return;
    setSettingsOpen(true);
    setSettingsMsg("");
    getMe(token)
      .then((m) => {
        setMe(m);
        setMeName(m.display_name);
      })
      .catch(() => {});
    listMemory(token).then(setMemory).catch(() => {});
  }

  async function saveProfile() {
    if (!token || !meName.trim()) return;
    try {
      await updateMe(token, meName.trim());
      setSettingsMsg("تم حفظ الاسم ✓");
    } catch (e) {
      setSettingsMsg(e instanceof Error ? e.message : "فشل الحفظ");
    }
  }

  async function savePassword() {
    if (!token || !pwCurrent || !pwNew) return;
    try {
      await changePassword(token, pwCurrent, pwNew);
      setPwCurrent("");
      setPwNew("");
      setSettingsMsg("تم تغيير كلمة السر ✓");
    } catch (e) {
      setSettingsMsg(e instanceof Error ? e.message : "فشل التغيير");
    }
  }

  async function addMemoryItem() {
    if (!token || !memContent.trim()) return;
    try {
      await addMemory(token, memKind, memContent.trim());
      setMemContent("");
      setMemory(await listMemory(token));
    } catch (e) {
      setSettingsMsg(e instanceof Error ? e.message : "فشل الحفظ");
    }
  }

  async function removeMemoryItem(id: string) {
    if (!token) return;
    try {
      await deleteMemory(token, id);
      setMemory((ms) => ms.filter((m) => m.id !== id));
    } catch {
      setSettingsMsg("فشل حذف العنصر");
    }
  }
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const t = localStorage.getItem("chatluck_token");
    if (!t) {
      router.replace("/login");
      return;
    }
    setToken(t);
  }, [router]);

  const refreshConversations = useCallback(async (t: string) => {
    try {
      setConversations(await listConversations(t));
    } catch {
      /* keep previous list */
    }
  }, []);

  useEffect(() => {
    if (!token) return;
    refreshConversations(token);
    listModels(token)
      .then((ms) => {
        setModels(ms.filter((m) => m.available && m.capabilities.includes("chat")));
        const first = ms.find((m) => m.available && m.capabilities.includes("chat"));
        if (first) setModel(first.id);
      })
      .catch(() => setStatus("تعذر تحميل النماذج"));
    agentCatalog(token).then(setAgents).catch(() => {});
  }, [token, refreshConversations]);

  useEffect(() => {
    if (!token || !activeConv) {
      setMessages([]);
      return;
    }
    listMessages(token, activeConv).then(setMessages).catch(() => {});
  }, [token, activeConv]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function onPickImage(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files) return;
    Array.from(files).slice(0, 3).forEach((f) => {
      if (!/^image\/(png|jpeg|webp|gif)$/.test(f.type)) {
        // Not an image: treat it as a document for analysis.
        onPickDocument(f);
        return;
      }
      if (f.size > 10 * 1024 * 1024) {
        setStatus("الصورة أكبر من 10 ميجا");
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const b64 = String(reader.result).split(",")[1] ?? "";
        setAttachments((a) => [
          ...a,
          { name: f.name, mime_type: f.type, data_base64: b64 },
        ]);
      };
      reader.readAsDataURL(f);
    });
    e.target.value = "";
  }

  useEffect(() => {
    if (!token || !searchQ.trim()) {
      setSearchResults(null);
      return;
    }
    const t = setTimeout(() => {
      searchConversations(token, searchQ.trim())
        .then(setSearchResults)
        .catch(() => setSearchResults([]));
    }, 350);
    return () => clearTimeout(t);
  }, [token, searchQ]);

  async function onPickDocument(f: File) {
    if (!token) return;
    if (f.size > 15 * 1024 * 1024) {
      setStatus("الملف أكبر من 15 ميجا");
      return;
    }
    setStatus(`جاري قراءة الملف ${f.name}…`);
    try {
      const r = await extractFile(token, f);
      if (!r.text.trim()) {
        setStatus("الملف فاضي أو مفيهوش نص قابل للقراءة");
        return;
      }
      setDocAttachment({
        name: f.name,
        type: r.type,
        characters: r.characters,
        truncated: r.truncated,
        text: r.text,
      });
      setStatus("");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "تعذر قراءة الملف");
    }
  }

  function appendMessage(msg: UiMessage) {
    setMessages((ms) => [...ms, msg]);
  }

  function patchLastMessage(patch: Partial<UiMessage>) {
    setMessages((ms) => {
      const copy = [...ms];
      const last = copy[copy.length - 1];
      copy[copy.length - 1] = { ...last, ...patch };
      return copy;
    });
  }

  async function refreshAfterNewConv(t: string, wasNew: boolean) {
    const convs = await listConversations(t);
    setConversations(convs);
    if (wasNew && convs.length > 0) setActiveConv(convs[0].id);
  }

  /** Stream an assistant reply for the current conversation. Shared by
   * send, regenerate and edit+resend. Retries once automatically on
   * transient provider overload (503/high demand). */
  async function runStream(
    finalText: string,
    opts: { regenerate?: boolean; editMessageId?: string; titleHint?: string } = {},
    atts: AttachmentIn[] = []
  ) {
    if (!token) return;
    const wasNew = !activeConv;
    setBusy(true);
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        const assistantMsg: UiMessage = {
          id: `local-a-${Date.now()}`,
          role: "assistant",
          content: "",
          streaming: true,
        };
        setMessages((ms) => [...ms, assistantMsg]);
        const controller = new AbortController();
        abortRef.current = controller;
        let failed: string | null = null;
        let threw: string | null = null;
        try {
          await streamChatAbortable(
            token,
            finalText,
            activeConv,
            model || null,
            {
              onToken: (content) => {
                setMessages((ms) => {
                  const copy = [...ms];
                  const last = copy[copy.length - 1];
                  copy[copy.length - 1] = { ...last, content: last.content + content };
                  return copy;
                });
              },
              onFallback: (from) => setStatus(`المزود ${from} لم يستجب، جاري التبديل…`),
              onError: (error) => {
                failed = error;
              },
              onDone: (provider, m) => {
                patchLastMessage({ provider, model: m, streaming: false });
              },
            },
            atts,
            controller.signal,
            opts
          );
        } catch (e) {
          if (!controller.signal.aborted) {
            threw = e instanceof Error ? e.message : "فشل الإرسال";
          }
        }
        patchLastMessage({ streaming: false });
        if (controller.signal.aborted) {
          setStatus("تم إيقاف التوليد");
          return;
        }
        const raw = threw ?? failed;
        if (!raw) {
          setStatus("");
          await refreshAfterNewConv(token, wasNew);
          return;
        }
        setMessages((ms) => ms.slice(0, -1));
        if (attempt === 0 && isTransientError(raw)) {
          setStatus("الخدمة مشغولة، بنحاول تلقائياً…");
          await new Promise((r) => setTimeout(r, 2500));
          continue;
        }
        setStatus(`خطأ: ${friendlyError(raw)}`);
        return;
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  async function onRegenerate() {
    if (!token || !activeConv || busy) return;
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    setMessages((ms) => ms.slice(0, ms.lastIndexOf(lastUser) + 1));
    await runStream(lastUser.content, { regenerate: true });
    listMessages(token, activeConv).then(setMessages).catch(() => {});
  }

  async function onSaveEdit(m: UiMessage) {
    if (!token || !activeConv || !editText.trim() || busy) return;
    const newText = editText.trim();
    setEditingId(null);
    setMessages((ms) => {
      const idx = ms.findIndex((x) => x.id === m.id);
      if (idx < 0) return ms;
      const copy = ms.slice(0, idx);
      copy.push({ ...m, content: newText });
      return copy;
    });
    await runStream(newText, { editMessageId: m.id });
    listMessages(token, activeConv).then(setMessages).catch(() => {});
  }

  async function send() {
    if (!token || busy) return;
    if (!input.trim() && !docAttachment) return;
    const hadDoc = docAttachment !== null;
    const text =
      input.trim() || "لخص الملف المرفق واستخرج أهم النقاط والمعلومات فيه.";
    setInput("");
    setStatus("");

    if (mode === "research") return sendResearch(text);
    if (mode === "image") return sendImage(text);

    let finalText = text;
    if (docAttachment) {
      const d = docAttachment;
      finalText =
        `الملف المرفق "${d.name}" (${d.type}${d.truncated ? "، مقتطع من الأصل" : ""}):\n\n` +
        `<document>\n${d.text}\n</document>\n\n` +
        `طلبي بخصوص الملف: ${text}`;
      setDocAttachment(null);
    }
    if (mode === "translate") {
      finalText = `ترجم النص التالي ترجمة احترافية تحفظ المعنى والسياق (وليست حرفية)، وحدد اللغة الهدف من طلبي أو ترجم للإنجليزية إن لم أحدد:\n\n${text}`;
    } else if (mode === "code") {
      finalText = `بصفتك مساعد برمجة خبير، ساعدني في التالي مع شرح واضح وأكواد جاهزة داخل code blocks:\n\n${text}`;
    }

    const atts: AttachmentIn[] = attachments.map((a) => ({
      mime_type: a.mime_type,
      data_base64: a.data_base64,
    }));
    setAttachments([]);
    appendMessage({
      id: `local-${Date.now()}`,
      role: "user",
      content:
        text +
        (atts.length ? `\n[${atts.length} صورة مرفقة]` : "") +
        (hadDoc ? `\n📄 ملف مرفق` : ""),
    });
    await runStream(finalText, { titleHint: text }, atts);
  }

  async function sendResearch(q: string) {
    if (!token) return;
    setBusy(true);
    setStatus("جاري البحث في الإنترنت…");
    appendMessage({ id: `local-${Date.now()}`, role: "user", content: q });
    try {
      const r = await research(token, q);
      appendMessage({
        id: `local-r-${Date.now()}`,
        role: "assistant",
        content: r.answer,
        provider: r.provider,
        model: r.model,
        sources: r.sources,
      });
      setStatus("");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "فشل البحث");
    } finally {
      setBusy(false);
    }
  }

  async function openGallery() {
    if (!token) return;
    setGalleryOpen(true);
    imageHistory(token).then(setGallery).catch(() => setStatus("تعذر تحميل المعرض"));
  }

  async function sendImage(prompt: string) {
    if (!token) return;
    setBusy(true);
    setStatus("جاري إنشاء الصورة…");
    appendMessage({ id: `local-${Date.now()}`, role: "user", content: `🎨 ${prompt}` });
    try {
      const [w, h] = imgSize.split("x").map(Number);
      const r = await generateImage(token, prompt, {
        width: w,
        height: h,
        style: imgStyle || undefined,
      });
      appendMessage({
        id: `local-i-${Date.now()}`,
        role: "assistant",
        content: `اتفضل الصورة (${r.provider}):`,
        imageUrl: r.url,
        provider: r.provider,
        model: r.model,
      });
      setStatus("");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "فشل إنشاء الصورة");
    } finally {
      setBusy(false);
    }
  }

  async function sendAgentTask() {
    if (!token || !agentTask.trim() || agentBusy) return;
    const task = agentTask.trim();
    setAgentTask("");
    setAgentsOpen(false);
    setAgentBusy(true);
    setStatus("المساعدون بيشتغلوا على المهمة…");
    appendMessage({ id: `local-${Date.now()}`, role: "user", content: `🤖 مهمة للمساعدين: ${task}` });
    try {
      const r = await runAgent(token, task);
      const lines = r.steps
        .map((s, i) => `**${i + 1}. ${s.agent}** — ${s.status}\n${s.output}`)
        .join("\n\n");
      const blockedNote = r.blocked.length
        ? `\n\n> خطوات محظورة تحتاج موافقتك: ${r.blocked.join("، ")}`
        : "";
      appendMessage({
        id: `local-ag-${Date.now()}`,
        role: "assistant",
        content: `## نتيجة تنفيذ المهمة\n\n${lines || "لم تُنفذ خطوات."}${blockedNote}`,
      });
      setStatus("");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "فشل تشغيل المساعد");
    } finally {
      setAgentBusy(false);
    }
  }

  async function onDeleteConv(id: string) {
    if (!token) return;
    try {
      await deleteConversation(token, id);
      if (activeConv === id) {
        setActiveConv(null);
        setMessages([]);
      }
      refreshConversations(token);
    } catch {
      setStatus("فشل حذف المحادثة");
    }
  }

  async function onRenameConv(id: string, oldTitle: string) {
    if (!token) return;
    const title = window.prompt("اسم المحادثة الجديد:", oldTitle);
    if (!title || !title.trim() || title.trim() === oldTitle) return;
    try {
      await renameConversation(token, id, title.trim());
      refreshConversations(token);
    } catch {
      setStatus("فشل إعادة التسمية");
    }
  }

  function copyMessage(m: UiMessage) {
    navigator.clipboard?.writeText(m.content).then(() => {
      setCopiedId(m.id);
      setTimeout(() => setCopiedId(null), 1500);
    });
  }

  function pickTool(modeOrAgents: Mode | "agents") {
    if (modeOrAgents === "agents") {
      setAgentsOpen(true);
      return;
    }
    setMode(modeOrAgents);
    setSidebarOpen(false);
  }

  function logout() {
    localStorage.removeItem("chatluck_token");
    router.replace("/login");
  }

  const isEmpty = messages.length === 0;

  return (
    <div className="shell">
      <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <h1>Chat Luck AI</h1>
        <button
          className="new-chat-btn"
          onClick={() => {
            setActiveConv(null);
            setMessages([]);
            setMode("chat");
            setSidebarOpen(false);
          }}
        >
          + محادثة جديدة
        </button>
        <input
          className="search-input"
          placeholder="ابحث في المحادثات…"
          value={searchQ}
          onChange={(e) => setSearchQ(e.target.value)}
        />
        {searchResults !== null && (
          <div className="search-results">
            {searchResults.length === 0 && (
              <div className="search-empty">لا توجد نتائج</div>
            )}
            {searchResults.map((r) => (
              <div
                key={r.id}
                role="button"
                tabIndex={0}
                className="conv-item"
                onClick={() => {
                  setActiveConv(r.id);
                  setSearchQ("");
                  setSidebarOpen(false);
                }}
                onKeyDown={(e) => e.key === "Enter" && setActiveConv(r.id)}
              >
                <span className="conv-title">{r.title}</span>
                <span className="search-snippet">{r.snippet}</span>
              </div>
            ))}
          </div>
        )}
        {searchResults === null && conversations.map((c) => (
          <div
            key={c.id}
            role="button"
            tabIndex={0}
            className={`conv-item ${c.id === activeConv ? "active" : ""}`}
            onClick={() => {
              setActiveConv(c.id);
              setSidebarOpen(false);
            }}
            onKeyDown={(e) => e.key === "Enter" && setActiveConv(c.id)}
          >
            <span className="conv-title">{c.title}</span>
            <span className="conv-actions">
              <button
                title="إعادة تسمية"
                onClick={(e) => {
                  e.stopPropagation();
                  onRenameConv(c.id, c.title);
                }}
              >
                ✏️
              </button>
              <button
                title="حذف"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteConv(c.id);
                }}
              >
                🗑️
              </button>
            </span>
          </div>
        ))}
        <div style={{ flex: 1 }} />
        <button className="settings-btn" onClick={openSettings}>
          ⚙ الإعدادات
        </button>
        <button className="logout-btn" onClick={logout}>
          تسجيل الخروج
        </button>
      </aside>

      <main className="main">
        <div className="topbar">
          <button className="menu-btn" onClick={() => setSidebarOpen(!sidebarOpen)}>
            ☰
          </button>
          <label htmlFor="model">النموذج:</label>
          <select
            id="model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
              </option>
            ))}
          </select>
          {status && <span className="status">{status}</span>}
        </div>

        <div className="messages">
          {isEmpty ? (
            <div className="welcome">
              <div className="logo">✦</div>
              <h2>أهلاً بيك في Chat Luck</h2>
              <p>مساعدك الذكي متعدد النماذج — اسأل، ابحث، ترجم، ابرمج، أو أنشئ صوراً.</p>
              <div className="tools-grid">
                {TOOLS.map((t) => (
                  <button key={t.mode} className="tool-card" onClick={() => pickTool(t.mode)}>
                    <div className="t-icon">{t.icon}</div>
                    <div className="t-name">{t.name}</div>
                    <div className="t-desc">{t.desc}</div>
                  </button>
                ))}
              </div>
              <div className="sugg-label">جرب تسأل:</div>
              <div className="sugg-grid">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    className="sugg-chip"
                    onClick={() => {
                      setInput(s);
                      setMode("chat");
                    }}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m, idx) => (
              <div key={m.id} className={`msg-row ${m.role}`}>
                <div className="avatar">{m.role === "user" ? "أنت" : "✦"}</div>
                <div className="bubble">
                  {editingId === m.id ? (
                    <div className="edit-box">
                      <textarea
                        rows={3}
                        value={editText}
                        onChange={(e) => setEditText(e.target.value)}
                      />
                      <div className="msg-actions">
                        <button onClick={() => onSaveEdit(m)}>حفظ وإعادة الإرسال</button>
                        <button onClick={() => setEditingId(null)}>إلغاء</button>
                      </div>
                    </div>
                  ) : m.role === "assistant" ? (
                    <div className={`md ${m.streaming ? "cursor" : ""}`}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {m.content || "…"}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    displayUserContent(m.content)
                  )}
                  {m.imageUrl && (
                    <a href={m.imageUrl} target="_blank" rel="noreferrer">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        className="gen-image"
                        src={m.imageUrl}
                        alt="صورة منشأة بالذكاء الاصطناعي"
                        onError={(e) => {
                          (e.target as HTMLImageElement).style.display = "none";
                        }}
                      />
                    </a>
                  )}
                  {m.sources && m.sources.length > 0 && (
                    <div className="sources">
                      {m.sources.map((s, i) => (
                        <a key={i} className="source-card" href={s.url} target="_blank" rel="noreferrer">
                          <span className="s-title">
                            [{i + 1}] {s.title}
                          </span>
                          <span className="s-url">{s.url}</span>
                        </a>
                      ))}
                    </div>
                  )}
                  {m.role === "assistant" && m.provider && (
                    <span className="meta">
                      {m.provider} / {m.model}
                    </span>
                  )}
                  {m.role === "assistant" && !m.streaming && (
                    <div className="msg-actions">
                      <button onClick={() => copyMessage(m)}>
                        {copiedId === m.id ? "✓ اتنسخ" : "نسخ"}
                      </button>
                      {idx === messages.length - 1 && activeConv && !busy && (
                        <button onClick={onRegenerate}>↻ إعادة التوليد</button>
                      )}
                      <button onClick={() => toggleSpeak(m)}>
                        {speakingId === m.id ? "⏸ إيقاف" : "🔊 استمع"}
                      </button>
                    </div>
                  )}
                  {m.role === "user" &&
                    !m.id.startsWith("local") &&
                    editingId !== m.id &&
                    activeConv &&
                    !busy && (
                      <div className="msg-actions">
                        <button
                          onClick={() => {
                            setEditingId(m.id);
                            setEditText(displayUserContent(m.content));
                          }}
                        >
                          تعديل
                        </button>
                      </div>
                    )}
                </div>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>

        <div className="composer-zone">
          {mode !== "chat" && (
            <div className="mode-bar">
              <span className="mode-chip">
                {MODE_META[mode].chip}
                <button onClick={() => setMode("chat")}>×</button>
              </span>
              {mode === "image" && (
                <>
                  <select className="img-opt" value={imgSize} onChange={(e) => setImgSize(e.target.value)}>
                    <option value="1024x1024">مربع 1:1</option>
                    <option value="1280x720">عريض 16:9</option>
                    <option value="720x1280">طولي 9:16</option>
                  </select>
                  <select className="img-opt" value={imgStyle} onChange={(e) => setImgStyle(e.target.value)}>
                    <option value="">بدون أسلوب</option>
                    <option value="realistic">واقعي</option>
                    <option value="anime">أنمي</option>
                    <option value="oil">رسم زيتي</option>
                    <option value="3d">ثلاثي الأبعاد</option>
                    <option value="fantasy">خيالي</option>
                  </select>
                  <button className="gallery-btn" onClick={openGallery}>
                    🖼 المعرض
                  </button>
                </>
              )}
            </div>
          )}
          {(attachments.length > 0 || docAttachment) && (
            <div className="attach-bar">
              {docAttachment && (
                <span className="attach-chip doc-chip">
                  📄 {docAttachment.name} ({docAttachment.type}
                  {docAttachment.truncated ? "، مقتطع" : ""})
                  <button onClick={() => setDocAttachment(null)}>×</button>
                </span>
              )}
              {attachments.map((a, i) => (
                <span key={i} className="attach-chip">
                  {a.name}
                  <button onClick={() => setAttachments((x) => x.filter((_, j) => j !== i))}>
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="composer">
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif,.pdf,.docx,.xlsx,.csv,.txt,.md,.json"
              multiple
              style={{ display: "none" }}
              onChange={onPickImage}
            />
            <button
              className="icon-btn"
              title="إرفاق صورة"
              onClick={() => fileRef.current?.click()}
              disabled={busy}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-label="إرفاق">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
            {speechSupported && (
              <button
                className={`icon-btn mic-btn ${listening ? "listening" : ""}`}
                title={listening ? "إيقاف الاستماع" : "اكتب بالصوت"}
                onClick={toggleDictation}
                disabled={busy}
              >
                🎙
              </button>
            )}
            <textarea
              rows={2}
              placeholder={listening ? "بسمعك… اتكلم دلوقتي" : MODE_META[mode].placeholder}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            {busy && mode === "chat" ? (
              <button
                className="send-btn stop-btn"
                title="إيقاف التوليد"
                onClick={() => abortRef.current?.abort()}
              >
                ■
              </button>
            ) : (
              <button className="send-btn" onClick={send} disabled={busy || (!input.trim() && !docAttachment)} title="إرسال">
                ➤
              </button>
            )}
          </div>
        </div>
      </main>

      {galleryOpen && (
        <div className="modal-back" onClick={() => setGalleryOpen(false)}>
          <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
            <button className="close-x" onClick={() => setGalleryOpen(false)}>
              ×
            </button>
            <h3>معرض الصور</h3>
            {gallery.length === 0 && (
              <div className="m-sub">لسه مفيش صور — أنشئ أول صورة من وضع الصور.</div>
            )}
            <div className="gallery-grid">
              {gallery.map((g) => (
                <a key={g.id} href={g.url} target="_blank" rel="noreferrer" className="gallery-item">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={g.url} alt={g.prompt} loading="lazy" />
                  <span className="gallery-prompt">{g.prompt}</span>
                </a>
              ))}
            </div>
          </div>
        </div>
      )}

      {settingsOpen && (
        <div className="modal-back" onClick={() => setSettingsOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="close-x" onClick={() => setSettingsOpen(false)}>
              ×
            </button>
            <h3>الإعدادات</h3>
            {settingsMsg && <div className="settings-msg">{settingsMsg}</div>}

            <div className="settings-section">
              <h4>الملف الشخصي</h4>
              <div className="m-sub">{me?.email || ""}</div>
              <div className="settings-row">
                <input
                  className="settings-input"
                  placeholder="اسمك"
                  value={meName}
                  onChange={(e) => setMeName(e.target.value)}
                />
                <button className="msg-actions-btn" onClick={saveProfile} disabled={!meName.trim()}>
                  حفظ
                </button>
              </div>
            </div>

            <div className="settings-section">
              <h4>تغيير كلمة السر</h4>
              <input
                className="settings-input"
                type="password"
                placeholder="كلمة السر الحالية"
                value={pwCurrent}
                onChange={(e) => setPwCurrent(e.target.value)}
              />
              <div className="settings-row">
                <input
                  className="settings-input"
                  type="password"
                  placeholder="كلمة السر الجديدة (8 أحرف على الأقل)"
                  value={pwNew}
                  onChange={(e) => setPwNew(e.target.value)}
                />
                <button className="msg-actions-btn" onClick={savePassword} disabled={!pwCurrent || pwNew.length < 8}>
                  تغيير
                </button>
              </div>
            </div>

            <div className="settings-section">
              <h4>المظهر</h4>
              <button className="msg-actions-btn" onClick={toggleTheme}>
                {theme === "dark" ? "☀ الوضع الفاتح" : "🌙 الوضع الداكن"}
              </button>
            </div>

            <div className="settings-section">
              <h4>ذاكرة المساعد</h4>
              <div className="m-sub">
                معلومات يتذكرها المساعد عنك لتخصيص إجاباته.
              </div>
              {memory.length === 0 && <div className="m-sub">لا توجد عناصر محفوظة بعد.</div>}
              {memory.map((m) => (
                <div key={m.id} className="memory-item">
                  <span className="memory-kind">
                    {m.kind === "preference" ? "تفضيل" : m.kind === "project" ? "مشروع" : m.kind === "task_history" ? "سجل" : "ملف"}
                  </span>
                  <span className="memory-content">{m.content}</span>
                  <button className="memory-del" onClick={() => removeMemoryItem(m.id)}>
                    ×
                  </button>
                </div>
              ))}
              <div className="settings-row" style={{ marginTop: 10 }}>
                <select className="settings-input" style={{ maxWidth: 110 }} value={memKind} onChange={(e) => setMemKind(e.target.value)}>
                  <option value="preference">تفضيل</option>
                  <option value="project">مشروع</option>
                </select>
                <input
                  className="settings-input"
                  placeholder="مثال: أفضّل الإجابات المختصرة"
                  value={memContent}
                  onChange={(e) => setMemContent(e.target.value)}
                />
                <button className="msg-actions-btn" onClick={addMemoryItem} disabled={!memContent.trim()}>
                  إضافة
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {agentsOpen && (
        <div className="modal-back" onClick={() => setAgentsOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="close-x" onClick={() => setAgentsOpen(false)}>
              ×
            </button>
            <h3>المساعدون المتخصصون</h3>
            <div className="m-sub">
              اكتب مهمة وسيخطط لها المساعد الرئيسي ويوزعها على الوكلاء المتخصصين.
            </div>
            <textarea
              rows={3}
              style={{
                width: "100%",
                background: "var(--panel-2)",
                border: "1px solid var(--hairline)",
                borderRadius: 12,
                color: "var(--ink)",
                padding: 12,
                fontFamily: "inherit",
                fontSize: 14,
                marginBottom: 14,
              }}
              placeholder="مثال: ابحث عن أحدث أخبار الذكاء الاصطناعي وترجمهم ملخصاً بالعربي"
              value={agentTask}
              onChange={(e) => setAgentTask(e.target.value)}
            />
            <button
              className="new-chat-btn"
              style={{ marginBottom: 16 }}
              disabled={agentBusy || !agentTask.trim()}
              onClick={sendAgentTask}
            >
              {agentBusy ? "جاري التنفيذ…" : "نفذ المهمة"}
            </button>
            {agents.map((a) => (
              <div key={a.name} className="agent-item">
                <div className="a-name">
                  {a.name} {a.sensitive ? "🔒" : ""}
                </div>
                <div className="a-desc">{a.description}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
