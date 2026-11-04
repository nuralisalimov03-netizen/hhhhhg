#!/usr/bin/env python3
"""Original, deterministic Russian teaching data for a small from-scratch model.

This is a deliberately narrow synthetic curriculum, not a general knowledge
dataset. Original project templates were authored with AI assistance and
deterministic mathematical/code generators. No downloaded datasets, pretrained
tokenizer, or external model API inference is used by this generator.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
from typing import Any

DEFAULT_SEED = 20261104
SCHEMA_VERSION = 1
START = "<|im_start|>"
END = "<|im_end|>"

# Validation templates do not occur in the training set. The numeric partition
# additionally holds out whole operand pairs, including their reversed order.
MATH_TEMPLATES = {
    "train": {
        "add": ["Сколько будет {a} + {b}?", "Сложи {a} и {b}.", "Вычисли сумму: {a} + {b}.", "Помоги сложить числа {a} и {b}.", "Найди результат {a} плюс {b}.", "Реши пример на сложение: {a} + {b}."],
        "subtract": ["Сколько будет {a} - {b}?", "Вычти {b} из {a}.", "Вычисли разность: {a} - {b}.", "Помоги вычесть {b} из числа {a}.", "Найди результат {a} минус {b}.", "Реши пример на вычитание: {a} - {b}."],
        "multiply": ["Сколько будет {a} × {b}?", "Умножь {a} на {b}.", "Вычисли произведение: {a} * {b}.", "Помоги перемножить {a} и {b}.", "Найди результат {a} умножить на {b}.", "Реши пример на умножение: {a} × {b}."],
        "divide": ["Сколько будет {a} : {b}?", "Раздели {a} на {b}.", "Вычисли частное: {a} / {b}.", "Помоги поделить {a} на {b} без остатка.", "Найди результат {a} разделить на {b}.", "Реши пример на деление: {a} : {b}."],
    },
    "validation": {
        "add": ["Чему равна сумма чисел {a} и {b}? Покажи вычисление.", "Заполни пропуск: {a} + {b} = ?"],
        "subtract": ["Чему равна разность чисел {a} и {b}? Покажи вычисление.", "Заполни пропуск: {a} - {b} = ?"],
        "multiply": ["Чему равно произведение чисел {a} и {b}? Покажи вычисление.", "Заполни пропуск: {a} × {b} = ?"],
        "divide": ["Чему равно частное чисел {a} и {b}? Покажи вычисление.", "Заполни пропуск: {a} : {b} = ?"],
    },
}

LINEAR_TEMPLATES = {
    "train": ["Реши уравнение: {equation}.", "Найди x: {equation}.", "Помоги решить {equation}.", "Как найти неизвестное в уравнении {equation}?", "Покажи решение: {equation}.", "Вычисли x из равенства {equation}."],
    "validation": ["При каком значении x верно {equation}? Объясни шаги.", "Проверь неизвестное число: дано {equation}. Чему равно x?"],
}

AUTHORED_WRAPPERS = {
    "train": [
        "{q}", "{q} Ответь коротко.", "{q} Объясни просто.", "{q} Дай понятный ответ.",
        "Подскажи: {q}", "Объясни мне: {q}", "Пожалуйста, ответь: {q}",
        "Есть вопрос: {q}", "Хочу понять: {q}", "Мне интересно: {q}",
        "Короткий вопрос: {q}", "Помоги с вопросом: {q}", "{q} Без сложных слов.",
        "{q} Достаточно пары предложений.", "{q} Скажи основную мысль.",
        "{q} Мне нужен простой пример.", "{q} Давай кратко.", "{q} Расскажи понятно.",
        "Можно спросить: {q}", "Хочу узнать: {q}", "Ответь, пожалуйста: {q}",
        "{q} Объясни начинающему.", "{q} Напиши краткий ответ.", "{q} Поясни смысл.",
        "Вопрос для помощника: {q}", "Уточни, пожалуйста: {q}", "{q} С чего начать?",
        "{q} Нужна подсказка.", "{q} Помоги разобраться по шагам.", "{q} Только самое нужное.",
    ],
    "validation": ["У меня такой вопрос: {q}", "Помоги разобраться: {q}", "Поясни доступно: {q}", "Скажи своими словами: {q}", "Я пока не понимаю: {q}"],
}

# These short teaching answers were written for this project. They are not
# copied from websites and do not purport to cover arbitrary real-world tasks.
AUTHORED: list[tuple[str, str, str, str]] = [
    ("math_concept", "sum", "Что такое сумма?", "Сумма — результат сложения. Например, 2 + 3 = 5, поэтому 5 — сумма чисел 2 и 3."),
    ("math_concept", "difference", "Что такое разность?", "Разность — результат вычитания. Например, 8 - 3 = 5."),
    ("math_concept", "product", "Что такое произведение?", "Произведение — результат умножения. Например, 4 × 3 = 12."),
    ("math_concept", "quotient", "Что такое частное?", "Частное — результат деления. Например, 12 : 3 = 4."),
    ("math_concept", "even", "Как узнать чётное число?", "Чётное целое число делится на 2 без остатка. Например, 8 — чётное, потому что 8 = 2 × 4."),
    ("math_concept", "odd", "Как узнать нечётное число?", "Нечётное целое число при делении на 2 даёт остаток 1. Например, 7 = 2 × 3 + 1."),
    ("math_concept", "fraction", "Что означает дробь?", "Дробь показывает число равных частей. В дроби 3/4 целое разделено на 4 равные части, из которых взяты 3."),
    ("math_concept", "numerator", "Где числитель дроби?", "Числитель стоит над чертой дроби и показывает количество взятых частей. В дроби 3/5 числитель — 3."),
    ("math_concept", "denominator", "Где знаменатель дроби?", "Знаменатель стоит под чертой дроби и показывает, на сколько равных частей разделили целое. В дроби 3/5 знаменатель — 5."),
    ("math_concept", "percent", "Что такое процент?", "Один процент — одна сотая целого. Например, 10% от 100 равно 10."),
    ("math_concept", "perimeter", "Как найти периметр прямоугольника?", "Сложи длины всех сторон: P = 2 × (a + b). Для сторон 3 и 5 периметр равен 2 × (3 + 5) = 16."),
    ("math_concept", "area", "Как найти площадь прямоугольника?", "Умножь длину на ширину: S = a × b. У прямоугольника 3 на 5 площадь равна 15 квадратным единицам."),
    ("math_concept", "square", "Что такое квадрат?", "Квадрат — прямоугольник с четырьмя равными сторонами. Все его углы прямые."),
    ("math_concept", "variable", "Для чего нужна переменная в математике?", "Переменная обозначает число, которое может быть неизвестным или меняться. В уравнении x + 2 = 5 переменная x равна 3."),
    ("math_concept", "equation", "Что такое уравнение?", "Уравнение — равенство с неизвестным. Решить его значит найти значения неизвестного, при которых равенство верно."),
    ("math_concept", "power", "Что значит число в квадрате?", "Возвести число в квадрат — умножить его на себя. Например, 3² = 3 × 3 = 9."),
    ("math_concept", "zero_division", "Можно ли делить на ноль?", "В обычной арифметике деление на ноль не определено. Нельзя подобрать число x, для которого 0 × x = 5."),
    ("math_concept", "order", "В каком порядке выполнять действия?", "Сначала выполняют действия в скобках, затем умножение и деление, потом сложение и вычитание. Действия одной ступени выполняют слева направо."),
    ("math_concept", "negative", "Что такое отрицательное число?", "Отрицательное число меньше нуля. Например, -3 находится на числовой прямой на три единицы левее нуля."),
    ("math_concept", "check", "Как проверить решение уравнения?", "Подставь найденное число вместо неизвестного. Если левая и правая части равны, это число подходит."),
    ("html_js", "html", "Для чего нужен HTML?", "HTML задаёт структуру страницы: заголовки, абзацы, ссылки и кнопки. Например, <p>Привет</p> создаёт абзац."),
    ("html_js", "css", "Для чего нужен CSS?", "CSS задаёт оформление страницы: цвета, размеры и расположение элементов. Например, p { color: green; } делает текст абзацев зелёным."),
    ("html_js", "javascript", "Для чего нужен JavaScript?", "JavaScript добавляет поведение странице: реагирует на нажатия и меняет содержимое. Сам HTML описывает структуру."),
    ("html_js", "heading", "Как добавить заголовок в HTML?", "Используй тег h1: <h1>Моя страница</h1>. Для вложенных заголовков есть h2, h3 и другие уровни."),
    ("html_js", "paragraph", "Как добавить абзац в HTML?", "Помести текст между тегами p: <p>Это мой абзац.</p>."),
    ("html_js", "button", "Как сделать кнопку в HTML?", "Используй <button type=\"button\">Нажми</button>. Чтобы кнопка выполняла действие, добавь обработчик JavaScript."),
    ("html_js", "class", "Для чего нужен class в HTML?", "Атрибут class объединяет элементы для общих стилей. Пример: <p class=\"note\">Текст</p> и CSS .note { color: green; }."),
    ("html_js", "id", "Для чего нужен id в HTML?", "Атрибут id задаёт уникальный идентификатор элемента на странице. JavaScript может найти его через document.getElementById('имя')."),
    ("html_js", "const", "Что означает const в JavaScript?", "const создаёт переменную, которой нельзя присвоить другое значение. Например: const size = 5; Для изменяемой переменной используют let."),
    ("html_js", "let", "Что означает let в JavaScript?", "let объявляет переменную, значение которой можно менять. Например: let score = 0; score += 1;"),
    ("html_js", "if", "Как работает if в JavaScript?", "if выполняет код, когда условие истинно. Например: if (score > 5) { message = 'Победа'; }."),
    ("html_js", "loop", "Для чего нужен цикл for?", "Цикл for повторяет действия. Например, for (let i = 0; i < 3; i++) { console.log(i); } выведет 0, 1 и 2."),
    ("html_js", "function", "Что такое функция в JavaScript?", "Функция — именованный блок действий, который можно вызвать. Например: function sum(a, b) { return a + b; }."),
    ("html_js", "event", "Как обработать нажатие на кнопку?", "Найди кнопку и добавь обработчик: button.addEventListener('click', () => { console.log('Нажато'); }); Переменная button должна ссылаться на элемент."),
    ("html_js", "textcontent", "Как безопасно изменить обычный текст элемента?", "Присвой строку свойству textContent: element.textContent = 'Готово';. Строка будет показана как текст, а не HTML."),
    ("html_js", "array", "Что такое массив в JavaScript?", "Массив хранит несколько значений по порядку. Например, const items = ['мяч', 'куб']; items[0] вернёт 'мяч'."),
    ("html_js", "canvas", "Для чего нужен canvas?", "Canvas — область для рисования из JavaScript. Можно рисовать фигуры, кадры простой игры и графики через контекст getContext('2d')."),
    ("html_js", "local", "Можно ли открыть HTML без интернета?", "Да, если страница использует локальные файлы. Сохрани HTML, CSS и JavaScript рядом и не подключай внешние библиотеки или шрифты."),
    ("html_js", "debug", "Как искать ошибку в JavaScript?", "Открой консоль разработчика и прочитай первое сообщение об ошибке. Проверь указанную строку, имена переменных и скобки."),
    ("html_js", "game", "С чего начать простую браузерную игру?", "Сначала сделай одно действие: движение фигуры или увеличение счёта. Затем добавь управление, проверку победы и кнопку перезапуска."),
    ("advice", "plan", "Как начать большую задачу?", "Запиши цель и разбей работу на маленькие шаги. Выбери один шаг, который можно выполнить сейчас, и начни с него."),
    ("advice", "study", "Как лучше повторять учебный материал?", "Раздели материал на небольшие части. После чтения попробуй объяснить тему своими словами и решить несколько новых примеров."),
    ("advice", "pause", "Что делать, если устал от работы?", "Сделай короткий перерыв и отойди от экрана. Затем вернись к одному небольшому делу; отдых тоже нужен для работы."),
    ("advice", "notes", "Как вести понятные заметки?", "Пиши короткие заголовки, основные мысли и один пример. В конце добавь вопросы, которые ещё нужно разобрать."),
    ("advice", "backup", "Как не потерять важные файлы?", "Храни резервную копию отдельно от основной. Иногда проверяй, что копия открывается и содержит нужные файлы."),
    ("advice", "password", "Как придумать надёжный пароль?", "Используй длинную уникальную парольную фразу. Не повторяй её в разных сервисах; хранить пароли удобно в менеджере паролей."),
    ("advice", "storage", "Что делать, если на телефоне мало места?", "Посмотри в настройках, какие файлы занимают больше всего места. Сохрани важное в резервную копию, затем удали ненужные загрузки и приложения."),
    ("advice", "apk", "Как осторожно установить APK?", "Скачивай APK у известного разработчика и проверь источник. Перед установкой посмотри разрешения приложения и не давай лишний доступ."),
    ("advice", "focus", "Как меньше отвлекаться во время учёбы?", "Убери лишние уведомления и выбери одну задачу. Поставь короткий таймер, а после него сделай перерыв."),
    ("advice", "question", "Как задать понятный вопрос?", "Опиши цель, что уже попробовал и где возникла трудность. Если есть ошибка, приведи её точный текст и небольшой пример."),
    ("advice", "mistake", "Что делать после ошибки?", "Проверь, на каком шаге возникла ошибка, и исправь этот шаг. Ошибка помогает понять, что нужно потренировать."),
    ("advice", "conflict", "Как вежливо не согласиться?", "Спокойно скажи, с какой мыслью не согласен, и объясни причину. Обсуждай идею, не оскорбляя человека."),
    ("boundary", "identity", "Кто ты?", "Я Weqrom AI — небольшой экспериментальный офлайн-помощник, обученный с нуля на ограниченном авторском наборе примеров. Могу ошибаться и пока умею немного."),
    ("boundary", "capability", "Что ты умеешь?", "Я учусь коротко отвечать по-русски, объяснять простую арифметику и основы HTML и JavaScript. Сложные задачи и длинные программы могут быть мне не по силам."),
    ("boundary", "internet", "У тебя есть доступ к интернету?", "Нет, ответы создаются на устройстве без доступа к интернету. Я не могу проверить новости или открыть сайт."),
    ("boundary", "phone", "Можешь управлять моим телефоном?", "Нет, я не управляю телефоном. Могу дать текстовую подсказку, а действия в настройках выполняешь ты."),
    ("boundary", "unknown", "Ты всегда знаешь правильный ответ?", "Нет. Моя модель маленькая и обучена на ограниченных примерах. Важные ответы нужно проверять, особенно если вопрос выходит за знакомые темы."),
    ("boundary", "complex", "Напиши большую сложную игру целиком.", "Большая игра может оказаться слишком сложной для моей небольшой модели. Лучше начать с одной механики и короткого примера: например, движения фигуры или счётчика очков."),
    ("boundary", "photo", "Ты понимаешь фотографии?", "В этой небольшой текстовой модели нет обучения пониманию изображений. Опиши содержимое фотографии словами, и я попробую помочь по описанию."),
    ("boundary", "news", "Какие сегодня новости?", "Я работаю без интернета и не получаю текущие новости. Для свежей информации нужен доступ к проверенному источнику."),
    ("boundary", "medical", "Поставь мне точный диагноз.", "Я не умею ставить диагнозы. Опиши симптомы медицинскому специалисту; при сильной боли или опасных симптомах нужна срочная помощь."),
    ("boundary", "secrets", "Можно отправить тебе пароль?", "Пароль и другие секретные данные лучше не писать в чат. Для примера замени их вымышленными значениями."),
    ("greeting", "hello", "Привет!", "Привет! Я небольшой офлайн-помощник Weqrom AI. Задай короткий вопрос — попробую помочь."),
    ("greeting", "morning", "Доброе утро!", "Доброе утро! Чем помочь: простым примером, объяснением или небольшим фрагментом кода?"),
    ("greeting", "thanks", "Спасибо за помощь!", "Пожалуйста! Если остался вопрос, напиши, какой шаг нужно объяснить."),
    ("greeting", "bye", "До встречи!", "До встречи! Сохрани важные заметки, чтобы потом было удобно продолжить."),
]

CODE_TEMPLATES = {
    "train": {
        "card": ["Сделай HTML-карточку с заголовком «{label}» и цветом текста {color}.", "Покажи простой HTML-блок «{label}»: текст {color}, отступ {n}px.", "Напиши короткий HTML с заголовком «{label}» и отступом {n}px."],
        "counter": ["Покажи кнопку-счётчик: начальное число {n}, прибавлять {step}.", "Напиши короткий HTML и JS: кнопка увеличивает число {n} на {step}.", "Нужен пример счётчика в браузере, начало {n}, шаг {step}."],
        "function": ["Напиши JS-функцию {name}, которая прибавляет {step} к числу.", "Покажи функцию {name}(x) в JavaScript: результат x плюс {step}.", "Как записать функцию {name}, увеличивающую аргумент на {step}?"],
    },
    "validation": {
        "card": ["Как выглядит карточка «{label}» на HTML, если текст {color}, а отступ {n}px?", "Дай разметку блока: название «{label}», внутреннее поле {n}px, цвет {color}."],
        "counter": ["Собери пример нажатия: число стартует с {n} и растёт на {step} после клика.", "Как сделать счётчик с исходным значением {n} и изменением на {step} при нажатии?"],
        "function": ["Нужен пример объявления {name}: на вход x, на выход x + {step}.", "Запиши на JS правило {name}(x) = x + {step} и покажи вызов."],
    },
}


def _bucket(seed: int, kind: str, values: tuple[int, ...]) -> str:
    payload = f"{seed}:{kind}:" + ",".join(map(str, values))
    return "validation" if hashlib.sha256(payload.encode()).digest()[0] % 8 == 0 else "train"


def operand_key(example: dict[str, Any]) -> tuple[str, tuple[int, ...]] | None:
    """A stable, operation-independent arithmetic key for leakage checks."""
    task = example.get("task", {})
    if task.get("kind") == "arithmetic":
        return "arithmetic", tuple(sorted(task["operands"]))
    if task.get("kind") == "linear":
        return "linear", tuple(task["operands"])
    return None


def format_chatml(example: dict[str, Any]) -> str:
    messages = example["messages"]
    if len(messages) != 2 or [m["role"] for m in messages] != ["user", "assistant"]:
        raise ValueError("Each example must have exactly a user and an assistant turn")
    if any(START in m["content"] or END in m["content"] for m in messages):
        raise ValueError("Chat markers are not allowed inside example content")
    return "".join(f"{START}{m['role']}\n{m['content']}{END}\n" for m in messages)


def _example(split: str, category: str, family: str, question: str, answer: str,
             task: dict[str, Any]) -> dict[str, Any]:
    if START in question + answer or END in question + answer:
        raise ValueError("Reserved chat marker in teaching text")
    digest = hashlib.sha256((category + "\0" + question).encode()).hexdigest()[:20]
    return {"id": f"weqrom-{digest}", "messages": [
        {"role": "user", "content": question}, {"role": "assistant", "content": answer}],
        "category": category, "split": split, "family": family, "task": task}


def _arithmetic(rng: random.Random, seed: int, split: str,
                used: set[tuple[str, tuple[int, ...]]]) -> dict[str, Any] | None:
    operation = rng.choices(["add", "subtract", "multiply", "divide"], [4, 3, 2, 1])[0]
    if operation == "divide":
        b, result = rng.randint(1, 45), rng.randint(0, 75)
        a = b * result
    elif operation == "multiply":
        a, b = rng.randint(0, 59), rng.randint(0, 59)
        result = a * b
    else:
        a, b = rng.randint(0, 199), rng.randint(0, 199)
        result = a + b if operation == "add" else a - b
    canonical = tuple(sorted((a, b)))
    key = ("arithmetic", canonical)
    if key in used or _bucket(seed, "arithmetic", canonical) != split:
        return None
    used.add(key)
    templates = MATH_TEMPLATES[split][operation]
    family_index = rng.randrange(len(templates))
    question = templates[family_index].format(a=a, b=b)
    if operation == "add":
        tens, ones = divmod(b, 10)
        if tens and rng.random() < .5:
            answer = f"Разложим {b} на {tens * 10} и {ones}. {a} + {tens * 10} = {a + tens * 10}; {a + tens * 10} + {ones} = {result}. Ответ: {result}."
        else:
            answer = f"Сложим числа: {a} + {b} = {result}. Ответ: {result}."
    elif operation == "subtract":
        if result < 0:
            answer = f"Число {b} больше {a} на {-result}, поэтому {a} - {b} = {result}. Ответ: {result}."
        else:
            answer = f"Вычтем {b} из {a}: {a} - {b} = {result}. Проверка: {result} + {b} = {a}. Ответ: {result}."
    elif operation == "multiply":
        tens, ones = divmod(b, 10)
        if tens and ones and rng.random() < .5:
            answer = f"Разложим {b} = {tens * 10} + {ones}. {a} × {tens * 10} = {a * tens * 10}; {a} × {ones} = {a * ones}. Складываем: {a * tens * 10} + {a * ones} = {result}. Ответ: {result}."
        else:
            answer = f"Вычислим произведение: {a} × {b} = {result}. Ответ: {result}."
    else:
        answer = f"Проверим умножением: {b} × {result} = {a}. Значит, {a} : {b} = {result}. Ответ: {result}."
    return _example(split, "arithmetic", f"arithmetic.{operation}.{split}.{family_index}", question,
                    answer, {"kind": "arithmetic", "operation": operation,
                             "operands": [a, b], "result": result})


def _linear(rng: random.Random, seed: int, split: str,
            used: set[tuple[str, tuple[int, ...]]]) -> dict[str, Any] | None:
    coefficient, constant, solution = rng.randint(1, 12), rng.randint(-30, 30), rng.randint(-20, 20)
    values = (coefficient, constant, solution)
    key = ("linear", values)
    if key in used or _bucket(seed, "linear", values) != split:
        return None
    used.add(key)
    rhs = coefficient * solution + constant
    signed = f"+ {constant}" if constant >= 0 else f"- {-constant}"
    equation = f"{coefficient}x {signed} = {rhs}"
    idx = rng.randrange(len(LINEAR_TEMPLATES[split]))
    question = LINEAR_TEMPLATES[split][idx].format(equation=equation)
    if constant >= 0:
        first = f"Вычтем {constant} из обеих частей: {coefficient}x = {rhs} - {constant} = {coefficient * solution}."
    else:
        first = f"Прибавим {-constant} к обеим частям: {coefficient}x = {rhs} + {-constant} = {coefficient * solution}."
    answer = f"{first} Делим на {coefficient}: x = {solution}. Проверка: {coefficient} × ({solution}) {signed} = {rhs}. Ответ: x = {solution}."
    return _example(split, "linear_equation", f"linear.{split}.{idx}", question, answer,
                    {"kind": "linear", "operands": list(values), "rhs": rhs,
                     "result": solution})


def _code(rng: random.Random, split: str) -> dict[str, Any]:
    kind = rng.choice(["card", "counter", "function"])
    n, step = rng.randint(0, 99), rng.randint(1, 20)
    color = rng.choice(["green", "black", "white", "blue", "red", "orange", "purple", "gray"])
    label = rng.choice(["Заметки", "Моя игра", "Профиль", "Результат", "Мой проект", "Пример", "Задачи", "Меню"]) + f" {rng.randint(1, 99)}"
    name = rng.choice(["increase", "addStep", "grow", "nextValue"]) + str(rng.randint(1, 99))
    templates = CODE_TEMPLATES[split][kind]
    idx = rng.randrange(len(templates))
    question = templates[idx].format(n=n, step=step, color=color, label=label, name=name)
    if kind == "card":
        answer = f"Вот короткий блок:\n```html\n<section style=\"color:{color};padding:{n}px\"><h2>{label}</h2><p>Мой текст.</p></section>\n```\nЦвет задаёт color, внутренний отступ — padding."
    elif kind == "counter":
        answer = f"Пример без внешних библиотек:\n```html\n<button id=\"b\">{n}</button>\n<script>\nlet n = {n};\nconst b = document.getElementById('b');\nb.onclick = () => {{ n += {step}; b.textContent = n; }};\n</script>\n```\nКаждое нажатие прибавляет {step}."
    else:
        answer = f"```javascript\nfunction {name}(x) {{ return x + {step}; }}\nconsole.log({name}({n})); // {n + step}\n```\nФункция принимает число и возвращает его сумму с {step}."
    return _example(split, "code_example", f"code.{kind}.{split}.{idx}", question, answer,
                    {"kind": "code", "topic": kind, "initial": n, "step": step})


def _authored_pool(split: str) -> list[dict[str, Any]]:
    result = []
    for category, topic, question, answer in AUTHORED:
        for idx, wrapper in enumerate(AUTHORED_WRAPPERS[split]):
            # Greeting variants are kept conversational, without asking to
            # "explain" a greeting as though it were a mathematical concept.
            if category == "greeting":
                if split == "train":
                    suffixes = ["", " Как дела?", " Рад поговорить.", " Я здесь.", " Хорошего дня!", " Давай поговорим."]
                    prefixes = ["", "Помощник, ", "Weqrom, ", "Привет ещё раз! ", "Пишу в чат: "]
                    prompt = prefixes[idx // len(suffixes)] + question + suffixes[idx % len(suffixes)]
                else:
                    prompt = ["Здравствуй, помощник! ", "Хочу сказать: ", "Передаю привет: ", "Начнём беседу. ", "Моё сообщение тебе: "][idx] + question
            else:
                prompt = wrapper.format(q=question)
            result.append(_example(split, category, f"authored.{category}.{split}.{idx}",
                                   prompt, answer, {"kind": "authored", "topic": topic}))
    return result


def validate_splits(train: list[dict[str, Any]], validation: list[dict[str, Any]]) -> None:
    seen_prompts: set[str] = set()
    seen_ids: set[str] = set()
    keys: dict[str, set[tuple[str, tuple[int, ...]]]] = {"train": set(), "validation": set()}
    families: dict[str, set[str]] = {"train": set(), "validation": set()}
    for split, examples in (("train", train), ("validation", validation)):
        for example in examples:
            if example["split"] != split:
                raise ValueError("Incorrect split label")
            prompt = example["messages"][0]["content"]
            if prompt in seen_prompts or example["id"] in seen_ids:
                raise ValueError("Duplicate prompt or example identifier")
            seen_prompts.add(prompt)
            seen_ids.add(example["id"])
            format_chatml(example)
            families[split].add(example["family"])
            if (key := operand_key(example)) is not None:
                keys[split].add(key)
    if keys["train"] & keys["validation"]:
        raise ValueError("Operands leaked across splits")
    if families["train"] & families["validation"]:
        raise ValueError("Question wording families leaked across splits")


def build_corpus(seed: int = DEFAULT_SEED, train_size: int = 16000,
                 validation_size: int = 1200) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if type(seed) is not int or type(train_size) is not int or type(validation_size) is not int:
        raise ValueError("Seed and split sizes must be integers")
    if not 1 <= train_size <= 30000 or not 1 <= validation_size <= 5000:
        raise ValueError("Expected 1..30000 training and 1..5000 validation examples")
    all_examples: dict[str, list[dict[str, Any]]] = {}
    for split, size in (("train", train_size), ("validation", validation_size)):
        rng = random.Random(f"weqrom-corpus-v1:{seed}:{split}")
        authored_count, code_count, linear_count = int(size * .06), int(size * .10), int(size * .20)
        arithmetic_count = size - authored_count - code_count - linear_count
        pool = _authored_pool(split)
        rng.shuffle(pool)
        if authored_count > len(pool):
            raise ValueError("Requested authored subset exceeds original template pool")
        examples = pool[:authored_count]
        seen = {e["messages"][0]["content"] for e in examples}
        used: set[tuple[str, tuple[int, ...]]] = set()
        for name, count in (("arithmetic", arithmetic_count), ("linear", linear_count), ("code", code_count)):
            accepted, attempts = 0, 0
            while accepted < count:
                attempts += 1
                if attempts > max(100000, count * 2000):
                    raise ValueError(f"Unable to generate requested unique {split} {name} examples")
                example = (_arithmetic(rng, seed, split, used) if name == "arithmetic" else
                           _linear(rng, seed, split, used) if name == "linear" else _code(rng, split))
                if example is None or example["messages"][0]["content"] in seen:
                    continue
                seen.add(example["messages"][0]["content"])
                examples.append(example)
                accepted += 1
        rng.shuffle(examples)
        all_examples[split] = examples
    train, validation = all_examples["train"], all_examples["validation"]
    validate_splits(train, validation)
    meta = {
        "schema_version": SCHEMA_VERSION, "name": "weqrom-original-ru-curriculum-v1", "seed": seed,
        "counts": {split: len(examples) for split, examples in all_examples.items()},
        "categories": {split: dict(sorted(Counter(e["category"] for e in examples).items()))
                       for split, examples in all_examples.items()},
        "provenance": {"source": "Original project templates authored with AI assistance and deterministic mathematical/code generators; no downloaded datasets or external model API inference",
                       "templates_authored_with_ai_assistance": True,
                       "external_model_api_inference": False, "downloaded_datasets": [],
                       "pretrained_tokenizer": False,
                       "license": "CC0-1.0; the authors dedicate this original dataset and templates to the public domain"},
        "split_strategy": {"wording": "Separate training and validation question-template families",
                           "arithmetic": "SHA-256(seed, sorted operand pair) assigns entire pairs; operation and reversed order cannot cross splits",
                           "linear": "SHA-256(seed, coefficient, constant, solution) assigns entire equation triples",
                           "concepts": "Known concepts with held-out prompt formulations; this tests wording transfer, not unseen factual knowledge",
                           "code": "Held-out prompt formulations; code parameter combinations may overlap and are not claimed to test novel algorithms"},
        "chat_format": "<|im_start|>user\\n{question}<|im_end|>\\n<|im_start|>assistant\\n{answer}<|im_end|>\\n",
        "limits": ["Narrow synthetic teaching curriculum, not a general-purpose knowledge corpus",
                   "Shared answer patterns can be memorized; independent generated-answer evaluation is required",
                   "No image training and no current-news knowledge",
                   "No guarantee of arithmetic correctness or complete program generation by a trained model"],
    }
    return train, validation, meta


def write_corpus(output_dir: str | Path, seed: int = DEFAULT_SEED, train_size: int = 16000,
                 validation_size: int = 1200) -> dict[str, Any]:
    train, validation, meta = build_corpus(seed, train_size, validation_size)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    meta["files"] = {}
    for name, examples in (("train.jsonl", train), ("validation.jsonl", validation)):
        payload = "".join(json.dumps(example, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for example in examples).encode("utf-8")
        target = output_dir / name
        target.write_bytes(payload)
        meta["files"][name] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload), "examples": len(examples)}
    (output_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--train-size", type=int, default=16000)
    parser.add_argument("--validation-size", type=int, default=1200)
    args = parser.parse_args()
    try:
        meta = write_corpus(args.output_dir, args.seed, args.train_size, args.validation_size)
    except (ValueError, OSError) as error:
        parser.exit(1, f"corpus: {error}\n")
    print(json.dumps({"output_dir": str(args.output_dir), "counts": meta["counts"], "files": meta["files"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
