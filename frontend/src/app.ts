type ModelResponse = {
  id: string;
  name: string;
  type: string;
  detail?: string;
  status?: string;
  message?: string;
  answer?: string;
};

const tabs = document.querySelectorAll<HTMLButtonElement>('.tab');
const contents = document.querySelectorAll<HTMLElement>('.tab-content');

for (const tab of tabs) {
  tab.addEventListener('click', () => {
    tabs.forEach((t) => t.classList.remove('active'));
    contents.forEach((c) => c.classList.remove('active'));
    tab.classList.add('active');
    const tabId = tab.dataset.tab;
    if (!tabId) return;
    document.getElementById(tabId)?.classList.add('active');
  });
}

const uploadForm = document.getElementById('upload-form') as HTMLFormElement;
const pathForm = document.getElementById('path-form') as HTMLFormElement;
const trainForm = document.getElementById('train-form') as HTMLFormElement;
const uploadResult = document.getElementById('upload-result') as HTMLParagraphElement;
const pathResult = document.getElementById('path-result') as HTMLParagraphElement;
const trainResult = document.getElementById('train-result') as HTMLParagraphElement;
const chatWindow = document.getElementById('chat-window') as HTMLDivElement;
const chatSend = document.getElementById('chat-send') as HTMLButtonElement;

uploadForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const formData = new FormData(uploadForm);
  const response = await fetch('/api/models/upload', { method: 'POST', body: formData });
  const data = (await response.json()) as ModelResponse;
  uploadResult.textContent = response.ok ? `Сохранено: ${data.id}` : `Ошибка: ${data.detail}`;
  if (response.ok) window.location.reload();
});

pathForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const formData = new FormData(pathForm);
  const response = await fetch('/api/models/register-path', { method: 'POST', body: formData });
  const data = (await response.json()) as ModelResponse;
  pathResult.textContent = response.ok ? `Добавлено: ${data.id}` : `Ошибка: ${data.detail}`;
  if (response.ok) window.location.reload();
});

(window as typeof window & { startModel: (modelId: string) => Promise<void> }).startModel = async (
  modelId: string,
) => {
  const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/start`, { method: 'POST' });
  const data = (await response.json()) as ModelResponse;
  alert(response.ok ? `Запущена: ${data.name}` : `Ошибка: ${data.detail}`);
  if (response.ok) window.location.reload();
};

trainForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const formData = new FormData(trainForm);
  const response = await fetch('/api/train', { method: 'POST', body: formData });
  const data = (await response.json()) as ModelResponse;
  trainResult.textContent = response.ok ? `${data.status}: ${data.message}` : `Ошибка: ${data.detail}`;
});

function appendChatMessage(roleClass: string, label: string, text: string): void {
  const msg = document.createElement('div');
  msg.className = `msg ${roleClass}`;
  msg.textContent = `${label}: ${text}`;
  chatWindow.appendChild(msg);
}

chatSend.addEventListener('click', async () => {
  const modelId = (document.getElementById('chat-model-id') as HTMLInputElement).value.trim();
  const systemPrompt = (document.getElementById('chat-system') as HTMLTextAreaElement).value;
  const message = (document.getElementById('chat-input') as HTMLInputElement).value.trim();
  if (!modelId || !message) return;

  appendChatMessage('user', 'Вы', message);
  (document.getElementById('chat-input') as HTMLInputElement).value = '';

  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_id: modelId, system_prompt: systemPrompt, message }),
  });
  const data = (await response.json()) as ModelResponse;
  appendChatMessage('bot', 'AI', response.ok ? data.answer ?? '' : data.detail ?? 'Unknown error');
  chatWindow.scrollTop = chatWindow.scrollHeight;
});
