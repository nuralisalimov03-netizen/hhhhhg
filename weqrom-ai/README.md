# Weqrom AI

[**Скачать готовый APK 1.1 — около 21 МБ**](https://raw.githubusercontent.com/nuralisalimov03-netizen/hhhhhg/downloads/offline-games-v1/Weqrom-AI-1.1.apk) · [SHA-256](../Weqrom-AI-SHA256SUMS.txt) · [Отчёт выпуска](reports/release-v11.json)

Отдельное приложение Android с собственной экспериментальной моделью **Weqrom Seed 10M**, обученной с нуля в этой работе. Готовые веса Qwen или другой языковой модели не использованы. Архитектура опирается на обычный Transformer семейства Llama; это не заявление о новой архитектуре или мощности уровня ChatGPT.

Модель, словарь, математический инструмент и конструктор игр входят в APK. Первый запуск проверяет встроенные веса и готовит их локально. Скачивание моделей, регистрация, сервер и API-ключи не нужны. В Android-манифесте нет разрешения на Интернет. Требуются Android 8+ ARM64 и актуальный Android System WebView; для указанного телефона с 8 ГБ RAM размер модели небольшой.

**Обновление 1.1:** устанавливается поверх Weqrom AI 1.0 с прежней подписью. Исправлено обучение: ответ видит весь вопрос, а все переменные параметры примеров кода указаны в запросе. Добавлены связанные диалоги. В истории больше нет скрытого обрезания отдельных сообщений; интерфейс сообщает, когда старые сообщения не вошли в контекст или ответ остановился по лимиту. Исправлены неоднозначные числа и нулевая степень в математическом инструменте.

## Что работает

- **Чат:** настоящая генерация собственной моделью, постепенный вывод, остановка и локальная история. Модель маленькая, обучалась на узком синтетическом наборе и может отвечать неверно или не по теме. Проверяйте существенные ответы.
- **Математика:** точные дроби, арифметика, линейные и квадратные уравнения с ограничениями на выражения, производные поддерживаемых функций. Вычисления выполняет отдельный офлайн-инструмент. Понятные математические выражения и короткие команды распознаются также в чате; такие ответы помечены «Офлайн-вычисление».
- **HTML-игры:** отдельный алгоритмический конструктор трёх полноценных основ: выживание, защита башнями и платформер. Можно выбрать название, тему, сложность, отредактировать полный HTML, запустить его и сохранить файл. Это не доказательство способности маленькой нейромодели самостоятельно программировать произвольные сложные игры.

Просмотр игры открывается в отдельном WebView без Java bridge и без доступа к сети, файлам или системным функциям. Экспорт через системное сохранение документов получает исходный HTML, а не изменённый DOM просмотра. История диалога остаётся в приватном хранилище приложения; её можно очистить. Сетевых сервисов и аналитики нет.

## Собственные веса и проверка

Обучение начинается со случайной инициализации **9 573 632 параметров**. Собственный SentencePiece BPE со словарём 4096 обучается только на исходной обучающей выборке. Версия 1.1 продолжает эти собственные веса на исправленном наборе: 16 000 обучающих, 1000 проверочных и 100 заранее замороженных итоговых заданий. Шаблоны созданы с помощью ИИ, математические ответы рассчитаны программно. Внешние датасеты и API других моделей для сбора примеров не использовались. [Происхождение и разделение данных](training/DATASET.md).

Код обучения — [train.py](training/train.py), генератор корпуса — [corpus.py](training/corpus.py), прямой экспорт своих весов — [export_gguf.py](training/export_gguf.py), проверка ответов — [evaluate.py](training/evaluate.py). Экспорт не загружает предобученные модели или токенизаторы. FP16 GGUF содержит около 19 МБ весов; нормы сохраняются в FP32. Шаблон диалога и токены конца ответа согласованы с обучением.

В 1.1 выполнены ещё 1800 шагов обучения на четырёх CPU-потоках; выбран checkpoint 3750 по проверочной потере, до открытия итогового теста. В нём сама модель прошла **46/100** заданий против **17/100** у 1.0. Учёт предыдущих сообщений: 8/10 против 0/10; копирование коротких данных: 9/20 против 0/20; выполнение указанных параметров в простых примерах HTML/JS: 5/20 против 0/20. Самостоятельные числовые ответы остаются слабыми: 4/30 против 2/30. Это проверка узких шаблонных задач; она не доказывает способность писать произвольные игры или решать сложную математику. [Карточка модели](MODEL_CARD.md), [сравнение](reports/v11-comparison.json), [все новые ответы, включая ошибки](reports/v11-candidate.json). Точные вычисления и полноценные игры обеспечивает отдельный код приложения.

Тесты корпуса проверяют ответы и отсутствие пересечений чисел/формулировок. Проверка причинности проверяет, что будущие токены не меняют предыдущие предсказания. На настоящих весах сравниваются идентификаторы токенов и все 4096 логитов PyTorch и llama.cpp. Эти проверки подтверждают перенос модели, а не её интеллект. Математические и игровые функции проверяются отдельно на настоящем коде в Chromium.

Веса не проверялись запуском на физическом Android-телефоне. Измерение на Linux не является измерением скорости телефона. Маленькая модель не заменяет универсальный сильный ИИ; подписка ChatGPT не предоставляет локальные веса моделей ChatGPT.

## Воспроизведение

В подготовленной среде используются Python 3.12, CPU PyTorch 2.6.0, SentencePiece 0.2.0, protobuf 5.29.5 и немодифицированный llama.cpp `a7a98e0fffed794396b3fbad4dcdbbc184963645`. Для Android — NDK r27d, SDK 35, ECJ/D8/aapt/zipalign/apksigner из общего кэша проекта.

```sh
python3 weqrom-ai/training/corpus.py --output-dir /workspace/.chits-tools/weqrom-corpus
PYTHONPATH=/workspace/.chits-tools/weqrom-training python3 weqrom-ai/training/train.py \
  --data-dir /workspace/.chits-tools/weqrom-corpus \
  --output-dir /workspace/.chits-tools/weqrom-seed-training \
  --steps 1500 --batch-size 8 --sequence-length 192 --threads 4
PYTHONPATH=/workspace/.chits-tools/weqrom-training python3 weqrom-ai/training/train.py \
  --data-dir /workspace/.chits-tools/weqrom-corpus \
  --output-dir /workspace/.chits-tools/weqrom-seed-training \
  --steps 2000 --batch-size 8 --sequence-length 192 --threads 4 \
  --learning-rate 0.0008 --authored-repeat 12 --code-repeat 3 --restart-schedule \
  --resume /workspace/.chits-tools/weqrom-seed-training/latest.pt
PYTHONPATH=/workspace/.chits-tools/weqrom-training python3 weqrom-ai/training/export_gguf.py \
  --checkpoint /workspace/.chits-tools/weqrom-seed-training/best.pt \
  --tokenizer /workspace/.chits-tools/weqrom-seed-training/tokenizer.model \
  --output weqrom-ai/app/src/main/assets/model/weqrom-seed-f16.gguf \
  --manifest weqrom-ai/app/src/main/assets/model/manifest.json
bash weqrom-ai/tools/build-apk.sh
```

Выше приведены исторические этапы 1.0. Для воспроизведения продолжения 1.1 сохраните исходный словарь и используйте полные диалоги:

```sh
python3 weqrom-ai/training/curriculum_v2.py \
  --output-dir /workspace/.chits-tools/weqrom-corpus-v2
mkdir -p /workspace/.chits-tools/weqrom-seed-v11
cp /workspace/.chits-tools/weqrom-seed-training/tokenizer.model /workspace/.chits-tools/weqrom-seed-v11/tokenizer.model
PYTHONPATH=/workspace/.chits-tools/weqrom-training python3 weqrom-ai/training/train.py \
  --data-dir /workspace/.chits-tools/weqrom-corpus-v2 \
  --output-dir /workspace/.chits-tools/weqrom-seed-v11 \
  --resume /workspace/.chits-tools/weqrom-seed-training/best.pt \
  --steps 3800 --batch-size 16 --sequence-length 128 --learning-rate 0.0006 \
  --threads 4 --seed 20261105 --objective supervised --restart-schedule \
  --eval-every 150 --eval-batches 8 --log-every 25 --save-every 150 --max-seconds 1500
PYTHONPATH=/workspace/.chits-tools/weqrom-training python3 weqrom-ai/training/export_gguf.py \
  --checkpoint /workspace/.chits-tools/weqrom-seed-v11/best.pt \
  --tokenizer /workspace/.chits-tools/weqrom-seed-v11/tokenizer.model \
  --output weqrom-ai/app/src/main/assets/model/weqrom-seed-f16.gguf \
  --manifest weqrom-ai/app/src/main/assets/model/manifest.json --name 'Weqrom Seed 10M 1.1'
WEQROM_BUILD_JOBS=2 bash weqrom-ai/tools/build-apk.sh
```

Итоговая проверка вызывает настоящий native-движок без математического инструмента и конструктора:

```sh
bash weqrom-ai/native/build-host.sh
python3 weqrom-ai/training/benchmark_v2.py \
  --expected-data-sha256 573849f41748c95d75e5ab17c9f468f3cc2ca3b174c79884b514b03e12099cc7 \
  --model weqrom-ai/app/src/main/assets/model/weqrom-seed-f16.gguf \
  --runner /workspace/.chits-tools/weqrom-ai-host/weqrom-ai-run \
  --output /tmp/weqrom-v11-benchmark.json
```

Команды обучения рассчитаны на отдельный пустой каталог результатов. Для продолжения собственных весов есть `--resume`; случайный seed фиксирован, но совпадение обучения побитно на другом оборудовании не гарантируется. Итоговый тест не используется при обучении или выборе checkpoint. После просмотра результатов новые эксперименты требуют нового независимого теста.

```sh
python3 -m unittest discover -s weqrom-ai/training -p 'test_*.py' -v
PYTHONPATH=/workspace/.chits-tools/weqrom-training python3 -m unittest discover -s weqrom-ai/tests -p 'test_*.py' -v
bash weqrom-ai/native/test-prompt.sh
```

Браузерным тестам нужен разрешённый loopback HTTP. Закрытый ключ совместимых обновлений находится только в `/workspace/.weqrom-ai-signing`; ключ и пароль нельзя печатать, включать в APK, исходный ZIP или Git. Большие промежуточные checkpoint и установленные зависимости хранятся вне репозитория.

Лицензии llama.cpp и Nerdamer включены в assets; оригинальные данные описаны отдельно в DATASET.md.
