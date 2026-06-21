import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { accountApi, authApi, notebookApi, streamChat } from "./api";
import { DOC_COLUMNS, STR } from "./strings";

const STORAGE_KEY = "iap_react_auth";
const ACTIVE_JOB = new Set(["running", "pausing", "stopping"]);
const RESUMABLE_DOC = new Set(["processing", "paused", "interrupted", "cancelled"]);
const WELCOME = { role: "assistant", content: STR.CHAT_WELCOME };

function uid() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

function loadAuth() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch {
    return null;
  }
}

function saveAuth(auth) {
  if (auth?.token) localStorage.setItem(STORAGE_KEY, JSON.stringify(auth));
  else localStorage.removeItem(STORAGE_KEY);
}

function detailText(result, fallback) {
  return String(result?.data?.detail || fallback);
}

function App() {
  const [auth, setAuth] = useState(() => loadAuth());
  const [page, setPage] = useState("notebook");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("e2e_token");
    if (!token) return;
    authApi.me(token).then((me) => {
      if (me.statusCode === 200) {
        const next = { token, user: me.data };
        saveAuth(next);
        setAuth(next);
        window.history.replaceState({}, "", window.location.pathname);
      }
    });
  }, []);

  const signIn = useCallback((nextAuth, message) => {
    saveAuth(nextAuth);
    setAuth(nextAuth);
    setNotice(message);
  }, []);

  const signOut = useCallback(() => {
    saveAuth(null);
    setAuth(null);
    setNotice("");
    setPage("notebook");
  }, []);

  if (!auth?.token) return <AuthScreen onSignIn={signIn} notice={notice} />;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">IA</div>
          <div>
            <h1>{STR.APP_TITLE}</h1>
            <p>{STR.APP_TAGLINE}</p>
          </div>
        </div>
        <nav aria-label="Primary">
          <a className={page === "notebook" ? "active" : ""} href="#notebook" onClick={() => setPage("notebook")}>
            {STR.NAV_NOTEBOOK}
          </a>
          <a className={page === "documents" ? "active" : ""} href="#documents" onClick={() => setPage("documents")}>
            {STR.NAV_DOCUMENTS}
          </a>
          <a className={page === "account" ? "active" : ""} href="#account" onClick={() => setPage("account")}>
            {STR.NAV_ACCOUNT}
          </a>
        </nav>
        <div className="user-card">
          <strong>{auth.user?.display_name || auth.user?.email || "User"}</strong>
          <span>{auth.user?.email}</span>
        </div>
      </aside>
      <main className="main-panel" data-testid="iap-react-app">
        {notice && <div className="toast success">{notice}</div>}
        {page === "notebook" && <NotebookPage auth={auth} />}
        {page === "documents" && <DocumentsPage token={auth.token} />}
        {page === "account" && <AccountPage auth={auth} setAuth={setAuth} onSignOut={signOut} />}
      </main>
    </div>
  );
}

function AuthScreen({ onSignIn, notice }) {
  const [tab, setTab] = useState("login");
  const [loginForm, setLoginForm] = useState({ email: "", password: "" });
  const [registerForm, setRegisterForm] = useState({ email: "", displayName: "", password: "", password2: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function completeAuth(token, message) {
    const me = await authApi.me(token);
    if (me.statusCode !== 200) {
      setError(STR.ERR_USER_INFO);
      return;
    }
    onSignIn({ token, user: me.data }, message);
  }

  async function login(event) {
    event.preventDefault();
    setError("");
    if (!loginForm.email || !loginForm.password) {
      setError(STR.ERR_EMAIL_PASSWORD_REQUIRED);
      return;
    }
    setBusy(true);
    const result = await authApi.login(loginForm.email, loginForm.password);
    setBusy(false);
    if (result.statusCode === 200 && result.data?.access_token) {
      await completeAuth(result.data.access_token, STR.SUCCESS_LOGIN);
    } else {
      setError(detailText(result, STR.ERR_LOGIN_FAILED));
    }
  }

  async function register(event) {
    event.preventDefault();
    setError("");
    if (registerForm.password !== registerForm.password2) {
      setError(STR.ERR_PASSWORD_MISMATCH);
      return;
    }
    if (registerForm.password.length < 8) {
      setError(STR.ERR_PASSWORD_TOO_SHORT);
      return;
    }
    setBusy(true);
    const result = await authApi.register(registerForm.email, registerForm.password, registerForm.displayName);
    setBusy(false);
    if (result.statusCode === 201 && result.data?.access_token) {
      await completeAuth(result.data.access_token, STR.SUCCESS_REGISTER);
    } else {
      setError(detailText(result, STR.ERR_REGISTER_FAILED));
    }
  }

  return (
    <div className="auth-layout" data-testid="iap-react-app">
      <section className="auth-hero">
        <div className="brand-mark large">IA</div>
        <h1>{STR.APP_TITLE}</h1>
        <p>{STR.APP_TAGLINE}</p>
        <div className="glow-card">RAG Notebook · Streaming Chat · Personal Knowledge Index</div>
      </section>
      <section className="auth-card">
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "login"} onClick={() => setTab("login")}>
            {STR.TAB_LOGIN}
          </button>
          <button role="tab" aria-selected={tab === "register"} onClick={() => setTab("register")}>
            {STR.TAB_REGISTER}
          </button>
        </div>
        {notice && <div className="toast success">{notice}</div>}
        {error && <div className="toast error">{error}</div>}
        {tab === "login" ? (
          <form role="tabpanel" aria-label={STR.TAB_LOGIN} onSubmit={login} className="form-grid">
            <label>
              {STR.LABEL_EMAIL}
              <input value={loginForm.email} onChange={(e) => setLoginForm({ ...loginForm, email: e.target.value })} />
            </label>
            <label>
              {STR.LABEL_PASSWORD}
              <input
                type="password"
                value={loginForm.password}
                onChange={(e) => setLoginForm({ ...loginForm, password: e.target.value })}
              />
            </label>
            <button className="primary" disabled={busy}>{STR.BTN_LOGIN}</button>
          </form>
        ) : (
          <form role="tabpanel" aria-label={STR.TAB_REGISTER} onSubmit={register} className="form-grid">
            <label>
              {STR.LABEL_EMAIL}
              <input value={registerForm.email} onChange={(e) => setRegisterForm({ ...registerForm, email: e.target.value })} />
            </label>
            <label>
              {STR.LABEL_DISPLAY_NAME}
              <input
                value={registerForm.displayName}
                onChange={(e) => setRegisterForm({ ...registerForm, displayName: e.target.value })}
              />
            </label>
            <label>
              {STR.LABEL_PASSWORD_MIN}
              <input
                type="password"
                value={registerForm.password}
                onChange={(e) => setRegisterForm({ ...registerForm, password: e.target.value })}
              />
            </label>
            <label>
              {STR.LABEL_CONFIRM_PASSWORD}
              <input
                type="password"
                value={registerForm.password2}
                onChange={(e) => setRegisterForm({ ...registerForm, password2: e.target.value })}
              />
            </label>
            <button className="primary" disabled={busy}>{STR.BTN_REGISTER}</button>
          </form>
        )}
      </section>
    </div>
  );
}

function NotebookPage({ auth }) {
  const [chatHistory, setChatHistory] = useState({});
  const [currentChatId, setCurrentChatId] = useState(null);
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState("");
  const [suggested, setSuggested] = useState([]);
  const [offerWebSearch, setOfferWebSearch] = useState(false);
  const [pendingWebQuery, setPendingWebQuery] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [steps, setSteps] = useState([]);
  const [tools, setTools] = useState([]);
  const bottomRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    async function loadHistory() {
      const ids = await notebookApi.historySessionIds(auth.token);
      if (ids.statusCode !== 200 || cancelled) return;
      const entries = {};
      for (const item of ids.data || []) {
        const sessionId = item?.[0];
        if (!sessionId) continue;
        const history = await notebookApi.historySession(auth.token, sessionId);
        entries[sessionId] = history.data?.result || [];
      }
      if (!cancelled) setChatHistory(entries);
    }
    loadHistory();
    return () => {
      cancelled = true;
    };
  }, [auth.token]);

  useEffect(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), [messages, streaming]);

  const submitPrompt = useCallback(
    async ({ prompt, confirmWebSearch = false, webSearchQuery = null }) => {
      if (streaming) return;
      const sessionId = currentChatId || uid();
      if (!currentChatId) setCurrentChatId(sessionId);

      const userMessage = prompt ? { role: "user", content: prompt } : null;
      const assistantId = uid();
      setMessages((prev) => [...prev, ...(userMessage ? [userMessage] : []), { id: assistantId, role: "assistant", content: "" }]);
      setSuggested([]);
      setOfferWebSearch(false);
      setSteps([]);
      setTools([]);
      setStreaming(true);

      let reply = "";
      let results = [];
      let finalSuggested = [];
      let nextOffer = false;
      let nextPending = "";
      const seenSteps = new Set();

      await streamChat(
        auth.token,
        {
          session_id: sessionId,
          content: prompt,
          confirm_web_search: confirmWebSearch,
          web_search_query: webSearchQuery,
        },
        (event) => {
          if (event.type === "error") {
            reply = `${STR.CHAT_REQUEST_FAILED}: ${event.message || ""}`;
            setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: reply } : m)));
            return;
          }
          if (event.type === "token") {
            reply += event.content || "";
            setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: reply } : m)));
          }
          if (["status", "node", "step"].includes(event.type)) {
            const message = event.message || event.node || "";
            const detail = event.detail || "";
            if ((event.type === "node" || event.type === "step") && message) {
              const key = `${message}|${detail}`;
              if (!seenSteps.has(key)) {
                seenSteps.add(key);
                setSteps((prev) => [...prev, { message, detail }]);
              }
            }
            if (event.tool) setTools((prev) => [...prev, { tool: event.tool, detail: detail || message }]);
          }
          if (event.type === "done") {
            reply = event.chat_response || reply;
            results = event.results || [];
            finalSuggested = event.suggested_questions || [];
            nextOffer = Boolean(event.offer_web_search);
            nextPending = event.pending_web_search_query || "";
            setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: reply, citations: results } : m)));
          }
        },
      );

      if (!reply) {
        reply = STR.CHAT_REQUEST_FAILED;
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: reply } : m)));
      }
      setChatHistory((prev) => ({
        ...prev,
        [sessionId]: [...(prev[sessionId] || []), { Question: prompt || webSearchQuery || nextPending || "(web search)", Response: reply }],
      }));
      setSuggested(finalSuggested);
      setOfferWebSearch(nextOffer);
      setPendingWebQuery(nextPending);
      setStreaming(false);
    },
    [auth.token, currentChatId, streaming],
  );

  function newChat() {
    setCurrentChatId(null);
    setMessages([WELCOME]);
    setSuggested([]);
    setOfferWebSearch(false);
    setPendingWebQuery("");
    setSteps([]);
    setTools([]);
  }

  function loadChat(sessionId, records) {
    setCurrentChatId(sessionId);
    setMessages([
      { role: "assistant", content: STR.CHAT_HISTORY_LOADED },
      ...records.flatMap((row) => [
        { role: "user", content: row.Question || "" },
        { role: "assistant", content: row.Response || "" },
      ]),
    ]);
    setOfferWebSearch(false);
    setPendingWebQuery("");
  }

  return (
    <section className="workspace-grid">
      <aside className="history-panel">
        <button className="secondary full" onClick={newChat}>{STR.BTN_NEW_CHAT}</button>
        <div className="history-list">
          {Object.entries(chatHistory).map(([sessionId, records]) => (
            <button key={sessionId} onClick={() => loadChat(sessionId, records)}>
              {records[0]?.Question ? `${records[0].Question.slice(0, 20)}...` : sessionId.slice(0, 8)}
            </button>
          ))}
        </div>
      </aside>
      <div className="chat-panel">
        <div className="chat-messages">
          {messages.map((msg, index) => (
            <ChatBubble key={msg.id || index} message={msg} />
          ))}
          {streaming && (
            <div className="stream-meta">
              <span>{STR.CHAT_THINKING}</span>
              {steps.length > 0 && <StepList steps={steps} />}
              {tools.length > 0 && <ToolList tools={tools} />}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
        {offerWebSearch && (
          <div className="suggestions">
            <span>{STR.WEB_SEARCH_HINT}</span>
            <button onClick={() => submitPrompt({ prompt: "", confirmWebSearch: true, webSearchQuery: pendingWebQuery || null })}>
              {STR.BTN_WEB_SEARCH}
            </button>
          </div>
        )}
        {suggested.length > 0 && (
          <div className="suggestions">
            <span>{STR.SUGGESTED_QUESTIONS_TITLE}</span>
            {suggested.map((question) => (
              <button key={question} onClick={() => submitPrompt({ prompt: question })}>{question}</button>
            ))}
          </div>
        )}
        <form
          className="chat-input"
          onSubmit={(event) => {
            event.preventDefault();
            const prompt = input.trim();
            if (!prompt) return;
            setInput("");
            submitPrompt({ prompt });
          }}
        >
          <input
            placeholder={STR.CHAT_INPUT_PLACEHOLDER}
            value={input}
            disabled={streaming}
            onChange={(event) => setInput(event.target.value)}
          />
          <button className="primary" disabled={streaming || !input.trim()}>Send</button>
        </form>
      </div>
    </section>
  );
}

function ChatBubble({ message }) {
  return (
    <article className={`chat-bubble ${message.role}`}>
      <ReactMarkdown>{String(message.content || "")}</ReactMarkdown>
      {Array.isArray(message.citations) && message.citations.length > 0 && (
        <div className="citations">
          <strong>{STR.CITATIONS_TITLE}</strong>
          {message.citations.map((cite, index) => (
            <p key={`${cite.file_name}-${index}`}>
              {index + 1}. {cite.file_name || ""} p.{cite.page || "?"} - {String(cite.snippet || "").slice(0, 200)}
            </p>
          ))}
        </div>
      )}
    </article>
  );
}

function StepList({ steps }) {
  return (
    <div>
      <strong>{STR.CHAT_STEPS_TITLE}</strong>
      {steps.map((step, index) => (
        <p key={`${step.message}-${index}`}>{STR.CHAT_STEP_DONE} {step.message}{step.detail ? ` - ${step.detail}` : ""}</p>
      ))}
    </div>
  );
}

function ToolList({ tools }) {
  return (
    <div>
      {tools.map((tool, index) => (
        <p key={`${tool.tool}-${index}`}>{STR.CHAT_TOOL_PREFIX}: <strong>{tool.tool}</strong> - {tool.detail}</p>
      ))}
    </div>
  );
}

function DocumentsPage({ token }) {
  const [docs, setDocs] = useState([]);
  const [file, setFile] = useState(null);
  const [activeJobId, setActiveJobId] = useState("");
  const [job, setJob] = useState(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  const loadDocs = useCallback(async () => {
    const result = await notebookApi.documents(token);
    const nextDocs = result.data?.documents || [];
    setDocs(nextDocs);
    const resumable = nextDocs.find((doc) => RESUMABLE_DOC.has(doc.status) && doc.job_id);
    if (resumable && !activeJobId) setActiveJobId(resumable.job_id);
  }, [activeJobId, token]);

  useEffect(() => {
    loadDocs();
  }, [loadDocs]);

  useEffect(() => {
    if (!activeJobId) return undefined;
    let cancelled = false;
    async function poll() {
      const result = await notebookApi.job(token, activeJobId);
      if (cancelled) return;
      if (result.statusCode === 200) {
        setJob(result.data);
        if (result.data?.status === "done") {
          setMessage(STR.DOCS_INDEX_DONE);
          setActiveJobId("");
          loadDocs();
        }
      }
    }
    poll();
    const id = window.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [activeJobId, loadDocs, token]);

  async function upload() {
    if (!file) return;
    setLoading(true);
    setMessage("");
    const result = await notebookApi.uploadDocument(token, file);
    setLoading(false);
    if ([200, 201].includes(result.statusCode) && result.data?.job_id) {
      setActiveJobId(result.data.job_id);
      setMessage(`${STR.DOCS_UPLOAD_OK} ${result.data.job_id.slice(0, 8)}`);
      await loadDocs();
    } else {
      setMessage(`${STR.DOCS_UPLOAD_FAIL} (HTTP ${result.statusCode})`);
    }
  }

  async function controlJob(action) {
    if (!activeJobId) return;
    if (action === "pause") await notebookApi.pauseJob(token, activeJobId);
    if (action === "resume") await notebookApi.resumeJob(token, activeJobId);
    if (action === "cancel") await notebookApi.cancelJob(token, activeJobId);
    const result = await notebookApi.job(token, activeJobId);
    if (result.statusCode === 200) setJob(result.data);
  }

  async function deleteDoc(doc) {
    await notebookApi.deleteDocument(token, doc.doc_id);
    if (activeJobId === doc.job_id) setActiveJobId("");
    await loadDocs();
  }

  return (
    <section className="page-card">
      <header>
        <h2>{STR.DOCS_TITLE}</h2>
        <p>{STR.DOCS_CAPTION}</p>
      </header>
      <div className="upload-box">
        <label>
          {STR.DOCS_UPLOADER}
          <input
            type="file"
            accept=".pdf,.doc,.docx,.ppt,.pptx,.txt,.md,.odt,.rtf"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
        </label>
        <button className="primary" onClick={upload} disabled={!file || loading}>
          {loading ? "上傳中... / Uploading..." : STR.BTN_UPLOAD_INDEX}
        </button>
      </div>
      {message && <div className="toast success">{message}</div>}
      {job && activeJobId && <JobProgress job={job} onControl={controlJob} />}
      <DocumentsTable docs={docs} onDelete={deleteDoc} onResume={(doc) => {
        setActiveJobId(doc.job_id);
        notebookApi.resumeJob(token, doc.job_id);
      }} />
    </section>
  );
}

function JobProgress({ job, onControl }) {
  const pct = Number(job.percent || 0);
  const status = job.status || "";
  return (
    <section className="job-card">
      <h3>{STR.DOCS_PROGRESS_TITLE}</h3>
      <div className="metric-row">
        <div><strong>{pct}%</strong><span>{STR.DOCS_PROGRESS_PERCENT}</span></div>
        <div><strong>{job.processed_pages || 0}/{job.total_pages || 0}</strong><span>{STR.DOCS_PROGRESS_PAGES.replace("{done}", job.processed_pages || 0).replace("{total}", job.total_pages || 0)}</span></div>
        <div><strong>{job.indexed_chunks || 0}</strong><span>chunks</span></div>
      </div>
      <div className="progress"><span style={{ width: `${pct}%` }} /></div>
      <p>{job.stage || status} - {job.message || ""}</p>
      <div className="button-row">
        {ACTIVE_JOB.has(status) && <button onClick={() => onControl("pause")}>{STR.BTN_PAUSE_INDEX}</button>}
        {["paused", "interrupted", "cancelled", "failed"].includes(status) && (
          <button onClick={() => onControl("resume")}>{STR.BTN_RESUME_INDEX}</button>
        )}
        {(ACTIVE_JOB.has(status) || ["paused", "interrupted"].includes(status)) && (
          <button onClick={() => onControl("cancel")}>{STR.BTN_STOP_INDEX}</button>
        )}
      </div>
      {status === "failed" && <div className="toast error">{job.message || STR.DOCS_INDEX_FAILED}</div>}
      {status === "paused" && <div className="toast info">{STR.DOCS_INDEX_PAUSED}</div>}
      {status === "interrupted" && <div className="toast warn">{STR.DOCS_INDEX_INTERRUPTED}</div>}
      {status === "cancelled" && <div className="toast info">{STR.DOCS_INDEX_STOPPED}</div>}
    </section>
  );
}

function DocumentsTable({ docs, onDelete, onResume }) {
  return (
    <section>
      <h3>{STR.DOCS_LIST_TITLE}</h3>
      {docs.length === 0 ? (
        <div className="empty">{STR.DOCS_EMPTY}</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>{DOC_COLUMNS.map(([, label]) => <th key={label}>{label}</th>)}<th>Actions</th></tr>
            </thead>
            <tbody>
              {docs.map((doc) => (
                <tr key={doc.doc_id}>
                  {DOC_COLUMNS.map(([key]) => <td key={key}>{String(doc[key] ?? "")}</td>)}
                  <td>
                    {RESUMABLE_DOC.has(doc.status) && doc.job_id && (
                      <button onClick={() => onResume(doc)}>{STR.BTN_RESUME_INDEX}</button>
                    )}
                    <button className="danger" onClick={() => onDelete(doc)}>
                      {STR.BTN_DELETE_DOC.replace("{name}", doc.filename || doc.doc_id)}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function AccountPage({ auth, setAuth, onSignOut }) {
  const [tab, setTab] = useState("profile");
  const [profile, setProfile] = useState(auth.user || {});
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyStatus, setApiKeyStatus] = useState(null);
  const [usage, setUsage] = useState({ summary: {}, daily: [], models: [] });
  const [message, setMessage] = useState("");

  useEffect(() => {
    authApi.me(auth.token).then((result) => {
      if (result.statusCode === 200) setProfile(result.data);
    });
    accountApi.apiKeyStatus(auth.token).then((result) => setApiKeyStatus(result.data || {}));
    Promise.all([
      accountApi.usageSummary(auth.token),
      accountApi.usageDaily(auth.token),
      accountApi.usageByModel(auth.token),
    ]).then(([summary, daily, models]) => {
      setUsage({
        summary: summary.data || {},
        daily: daily.data?.series || [],
        models: models.data?.models || [],
      });
    });
  }, [auth.token]);

  async function saveName() {
    const result = await accountApi.updateProfile(auth.token, { display_name: profile.display_name || "" });
    if (result.statusCode === 200) {
      const next = { ...auth, user: { ...auth.user, display_name: profile.display_name } };
      saveAuth(next);
      setAuth(next);
      setMessage(STR.SUCCESS_UPDATED);
    } else {
      setMessage(STR.ERR_UPDATE_FAILED);
    }
  }

  async function updatePassword() {
    const result = await accountApi.updateProfile(auth.token, {
      password: newPassword,
      current_password: currentPassword,
    });
    setMessage(result.statusCode === 200 ? STR.SUCCESS_PASSWORD_UPDATED : detailText(result, STR.ERR_UPDATE_FAILED));
  }

  async function saveKey() {
    if (apiKey.length < 10) {
      setMessage(STR.ERR_KEY_TOO_SHORT);
      return;
    }
    const result = await accountApi.setApiKey(auth.token, apiKey);
    setMessage(result.statusCode === 200 ? STR.SUCCESS_KEY_SAVED : STR.ERR_KEY_SAVE_FAILED);
    const status = await accountApi.apiKeyStatus(auth.token);
    setApiKeyStatus(status.data || {});
  }

  async function clearKey() {
    await accountApi.deleteApiKey(auth.token);
    setMessage(STR.SUCCESS_KEY_CLEARED);
    setApiKeyStatus({ configured: false });
  }

  return (
    <section className="page-card">
      <header><h2>{STR.ACCOUNT_TITLE}</h2></header>
      {message && <div className="toast info">{message}</div>}
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "profile"} onClick={() => setTab("profile")}>{STR.TAB_PROFILE}</button>
        <button role="tab" aria-selected={tab === "apikey"} onClick={() => setTab("apikey")}>{STR.TAB_API_KEY}</button>
        <button role="tab" aria-selected={tab === "usage"} onClick={() => setTab("usage")}>{STR.TAB_USAGE}</button>
      </div>
      {tab === "profile" && (
        <div className="form-grid">
          <p><strong>{STR.LABEL_EMAIL_READONLY}:</strong> {profile.email || ""}</p>
          <label>{STR.LABEL_NICKNAME}<input value={profile.display_name || ""} onChange={(e) => setProfile({ ...profile, display_name: e.target.value })} /></label>
          <button className="primary" onClick={saveName}>{STR.BTN_SAVE_NAME}</button>
          <details>
            <summary>{STR.EXPANDER_PASSWORD}</summary>
            <label>{STR.LABEL_CURRENT_PASSWORD}<input type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} /></label>
            <label>{STR.LABEL_NEW_PASSWORD}<input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} /></label>
            <button onClick={updatePassword}>{STR.BTN_UPDATE_PASSWORD}</button>
          </details>
          <button className="danger" onClick={onSignOut}>{STR.BTN_LOGOUT}</button>
        </div>
      )}
      {tab === "apikey" && (
        <div className="form-grid">
          <p>{STR.API_KEY_STATUS} {apiKeyStatus?.configured ? STR.API_KEY_CONFIGURED : STR.API_KEY_NOT_CONFIGURED}</p>
          {apiKeyStatus?.updated_at && <p>{STR.API_KEY_LAST_UPDATED} {apiKeyStatus.updated_at}</p>}
          <label>{STR.LABEL_OPENAI_KEY}<input type="password" placeholder="sk-..." value={apiKey} onChange={(e) => setApiKey(e.target.value)} /></label>
          <div className="button-row">
            <button className="primary" onClick={saveKey}>{STR.BTN_SAVE_KEY}</button>
            <button onClick={clearKey} disabled={!apiKeyStatus?.configured}>{STR.BTN_CLEAR_KEY}</button>
          </div>
          <div className="toast info">{STR.API_KEY_INFO}</div>
        </div>
      )}
      {tab === "usage" && <UsagePanel usage={usage} />}
    </section>
  );
}

function UsagePanel({ usage }) {
  const summary = usage.summary || {};
  if (!summary.langfuse_enabled) {
    return <div className="toast warn">{summary.note || STR.USAGE_LANGFUSE_DISABLED}</div>;
  }
  const today = summary.today || {};
  const month = summary.month || {};
  return (
    <div className="usage-grid">
      <Metric label={STR.METRIC_TODAY_TOKENS} value={(today.tokens || 0).toLocaleString()} />
      <Metric label={STR.METRIC_TODAY_COST} value={`$${Number(today.cost_usd || 0).toFixed(4)}`} />
      <Metric label={STR.METRIC_MONTH_TOKENS} value={(month.tokens || 0).toLocaleString()} />
      <Metric label={STR.METRIC_MONTH_COST} value={`$${Number(month.cost_usd || 0).toFixed(4)}`} />
      <MiniChart title={STR.CHART_TOKEN_TREND} rows={usage.daily} field="tokens" />
      <MiniChart title={STR.CHART_COST_TREND} rows={usage.daily} field="cost_usd" />
      <div className="table-wrap span-2">
        <h3>{STR.USAGE_BY_MODEL}</h3>
        <table>
          <tbody>
            {usage.models.map((row, index) => (
              <tr key={row.model || index}>{Object.entries(row).map(([key, value]) => <td key={key}>{String(value)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Metric({ label, value }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function MiniChart({ title, rows, field }) {
  const values = rows.map((row) => Number(row[field] || 0));
  const max = Math.max(...values, 1);
  const points = values.map((value, index) => `${(index / Math.max(values.length - 1, 1)) * 100},${40 - (value / max) * 36 + 2}`).join(" ");
  return (
    <div className="chart-card">
      <h3>{title}</h3>
      <svg viewBox="0 0 100 44" preserveAspectRatio="none" aria-hidden="true">
        <polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" />
      </svg>
    </div>
  );
}

export default App;
