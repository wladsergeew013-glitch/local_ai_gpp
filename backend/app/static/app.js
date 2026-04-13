"use strict";
const tabs = document.querySelectorAll('.tab');
const contents = document.querySelectorAll('.tab-content');
for (const tab of tabs) {
    tab.addEventListener('click', () => {
        tabs.forEach((t) => t.classList.remove('active'));
        contents.forEach((c) => c.classList.remove('active'));
        tab.classList.add('active');
        const tabId = tab.dataset.tab;
        if (!tabId)
            return;
        document.getElementById(tabId)?.classList.add('active');
    });
}
const uploadForm = document.getElementById('upload-form');
const pathForm = document.getElementById('path-form');
const trainForm = document.getElementById('train-form');
const uploadResult = document.getElementById('upload-result');
const pathResult = document.getElementById('path-result');
const trainResult = document.getElementById('train-result');
const chatWindow = document.getElementById('chat-window');
const chatSend = document.getElementById('chat-send');
uploadForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(uploadForm);
    const response = await fetch('/api/models/upload', { method: 'POST', body: formData });
    const data = (await response.json());
    uploadResult.textContent = response.ok ? `Сохранено: ${data.id}` : `Ошибка: ${data.detail}`;
    if (response.ok)
        window.location.reload();
});
pathForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(pathForm);
    const response = await fetch('/api/models/register-path', { method: 'POST', body: formData });
    const data = (await response.json());
    pathResult.textContent = response.ok ? `Добавлено: ${data.id}` : `Ошибка: ${data.detail}`;
    if (response.ok)
        window.location.reload();
});
window.startModel = async (modelId) => {
    const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/start`, { method: 'POST' });
    const data = (await response.json());
    alert(response.ok ? `Запущена: ${data.name}` : `Ошибка: ${data.detail}`);
    if (response.ok)
        window.location.reload();
};
trainForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(trainForm);
    const response = await fetch('/api/train', { method: 'POST', body: formData });
    const data = (await response.json());
    trainResult.textContent = response.ok ? `${data.status}: ${data.message}` : `Ошибка: ${data.detail}`;
});
function appendChatMessage(roleClass, label, text) {
    const msg = document.createElement('div');
    msg.className = `msg ${roleClass}`;
    msg.textContent = `${label}: ${text}`;
    chatWindow.appendChild(msg);
}
chatSend.addEventListener('click', async () => {
    const modelId = document.getElementById('chat-model-id').value.trim();
    const systemPrompt = document.getElementById('chat-system').value;
    const message = document.getElementById('chat-input').value.trim();
    if (!modelId || !message)
        return;
    appendChatMessage('user', 'Вы', message);
    document.getElementById('chat-input').value = '';
    const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: modelId, system_prompt: systemPrompt, message }),
    });
    const data = (await response.json());
    appendChatMessage('bot', 'AI', response.ok ? data.answer ?? '' : data.detail ?? 'Unknown error');
    chatWindow.scrollTop = chatWindow.scrollHeight;
});
