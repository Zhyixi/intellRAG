const API_BASE = "/api/v1";

async function parseJson(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

async function request(path, { token, headers, ...options } = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
  });
  const data = await parseJson(response);
  return { data, statusCode: response.status };
}

export const authApi = {
  register: (email, password, displayName) =>
    request("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, display_name: displayName }),
    }),
  login: (email, password) =>
    request("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: (token) => request("/auth/me", { token }),
};

export const notebookApi = {
  historySessionIds: (token) => request("/notebook/history/session_ids", { token }),
  historySession: (token, sessionId) =>
    request(`/notebook/history/session?session_id=${encodeURIComponent(sessionId)}`, { token }),
  uploadDocument: (token, file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/notebook/documents/upload", {
      token,
      method: "POST",
      body: form,
    });
  },
  documents: (token) => request("/notebook/documents", { token }),
  deleteDocument: (token, docId) =>
    request(`/notebook/documents/${encodeURIComponent(docId)}`, { token, method: "DELETE" }),
  job: (token, jobId) => request(`/notebook/jobs/${encodeURIComponent(jobId)}`, { token }),
  pauseJob: (token, jobId) =>
    request(`/notebook/jobs/${encodeURIComponent(jobId)}/pause`, { token, method: "POST" }),
  cancelJob: (token, jobId) =>
    request(`/notebook/jobs/${encodeURIComponent(jobId)}/cancel`, { token, method: "POST" }),
  resumeJob: (token, jobId) =>
    request(`/notebook/jobs/${encodeURIComponent(jobId)}/resume`, { token, method: "POST" }),
};

export async function streamChat(token, payload, onEvent) {
  const response = await fetch(`${API_BASE}/notebook/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      syslang: "zh",
      confirm_web_search: false,
      web_search_query: null,
      ...payload,
    }),
  });

  if (!response.ok || !response.body) {
    onEvent({ type: "error", message: `HTTP ${response.status}` });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        onEvent(JSON.parse(line.slice(6)));
      } catch {
        // Ignore malformed keepalive or partial SSE lines.
      }
    }
  }
}

export const accountApi = {
  updateProfile: (token, payload) =>
    request("/account/profile", { token, method: "PATCH", body: JSON.stringify(payload) }),
  apiKeyStatus: (token) => request("/account/api-key", { token }),
  setApiKey: (token, apiKey) =>
    request("/account/api-key", {
      token,
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey }),
    }),
  deleteApiKey: (token) => request("/account/api-key", { token, method: "DELETE" }),
  usageSummary: (token) => request("/account/usage/summary", { token }),
  usageDaily: (token, days = 30) => request(`/account/usage/daily?days=${days}`, { token }),
  usageByModel: (token, days = 30) => request(`/account/usage/by-model?days=${days}`, { token }),
};
