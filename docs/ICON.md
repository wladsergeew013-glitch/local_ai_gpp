# Иконка мини-агента

Иконка использует голову исходного персонажа из `frontend/public/assistant/frames/ready_0.png`: тёмная причёска, голубые глаза, дружелюбное выражение. Исходная анимация помощника сохранена.

Изображение создано встроенным инструментом ImageGen, режим `identity-preserve`, прозрачный фон. Мастер: `models_storage/branding/icons/mini_agent_head_v2.png`. ICO: `mini_agent_head_v2.ico`, размеры 16, 20, 24, 32, 40, 48, 64, 128 и 256 пикселей. Преобразование формата: `python tools/make_windows_icon.py` (Pillow).

ICO встроен в EXE и используется окном WebView и окнами Tk. Для панели задач задан постоянный AppUserModelID `LocalAI.GPP.Desktop`. Значок трея использует тот же прозрачный PNG. Ресурсы включены в EXE, поэтому перенос папки или замена каталога моделей не убирает иконку.

## Итоговый промпт

Use case: identity-preserve. Asset type: Windows application, taskbar and notification tray icon for Local AI. Input image: reference/edit target is the existing pixel-art mini-agent. Primary request: turn ONLY his head into a highly detailed, polished character icon. Keep the same recognizable friendly young human character, fair warm skin, large bright blue eyes, swept dark navy hair with its distinctive voluminous side quiff, small ears, eyebrows and gentle confident smile. Preserve his identity and approachable miniature assistant character proportions. Style: refined hand-painted 3D game-character illustration, rich but controlled hair strands and highlights, subtle face shading, crisp clean silhouette; detailed, not photorealistic. Composition: head only, no shoulders, no body, no clothing. Straight-on centered face, square image, hair-to-chin head fills about 88% of canvas, safe narrow margin. Actual transparent background with clean alpha edges. Design must remain recognizable at 16 and 32 pixel icon sizes: strong hair silhouette, clear bright eyes, uncluttered face. No background disc, frame, letters, text, watermark, robot parts, extra objects or characters.
