import asyncio
import os
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import httpx
import uuid

load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GIGACHAT_AUTH_KEY = os.getenv('GIGACHAT_AUTH_KEY')
GIGACHAT_SCOPE = os.getenv('GIGACHAT_SCOPE', 'GIGACHAT_API_PERS')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


async def get_gigachat_token() -> str:
    url = 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth'
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
        'RqUID': str(uuid.uuid4()),
        'Authorization': f'Basic {GIGACHAT_AUTH_KEY}',
    }
    data = {'scope': GIGACHAT_SCOPE}
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        token = response.json()['access_token']
        logger.info('GigaChat token obtained')
        return token


async def generate_tarot_gigachat(prompt: str) -> str:
    token = await get_gigachat_token()
    url = 'https://gigachat.devices.sberbank.ru/api/v1/chat/completions'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {token}',
    }
    system_prompt = (
        'You are a mystical tarot oracle with years of experience. '
        'Always respond in Russian. '
        'Format your response strictly: '
        '1. Bold header with tarot spread name and emoji. '
        '2. For each card: emoji on new line, then CARD NAME IN CAPS, '
        'then position in italics, then interpretation paragraph. '
        '3. Final section with header OBSHCHIY VYVOD (general conclusion). '
        'Use tarot symbolism and archetypes. Be poetic and vivid.'
    )
    payload = {
        'model': 'GigaChat',
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.95,
        'max_tokens': 1800,
        'stream': False,
    }
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(url, headers=headers, json=payload, timeout=90)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']
        logger.info('GigaChat response: %d chars', len(content))
        return content


class TarotStates(StatesGroup):
    waiting_for_spread = State()


bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

SPREADS_MENU = (
    'Выбери тип расклада или задай свой вопрос:\n\n'
    'Карта дня - одна карта на сегодня\n'
    'Три карты - прошлое, настоящее, будущее\n'
    'Кельтский крест - глубокий расклад из 10 карт\n'
    'Да/Нет - ответ на конкретный вопрос\n'
    'Любовь - расклад на отношения\n'
    'Путь - расклад на жизненный путь\n\n'
    'Или напиши свой вопрос - и карты ответят'
)


@dp.message(Command('start'))
async def cmd_start(message: Message):
    logger.info('User %d started the bot', message.from_user.id)
    await message.answer(
        'Добро пожаловать в Оракул Таро!\n\n'
        'Карты таро открывают тайны прошлого, настоящего и будущего.\n\n'
        'Напиши /tarot - и начнём твоё гадание\n'
        'Напиши /help - список команд'
    )


@dp.message(Command('help'))
async def cmd_help(message: Message):
    await message.answer(
        'Команды бота:\n\n'
        '/tarot - начать гадание на картах таро\n'
        '/start - приветствие\n'
        '/help - эта справка\n\n'
        'Бот работает 24/7 без ограничений на количество запросов'
    )


@dp.message(Command('tarot'))
async def cmd_tarot(message: Message, state: FSMContext):
    logger.info('User %d requested tarot', message.from_user.id)
    await state.set_state(TarotStates.waiting_for_spread)
    await message.answer(SPREADS_MENU)


@dp.message(TarotStates.waiting_for_spread)
async def process_spread(message: Message, state: FSMContext):
    await state.clear()
    user_request = message.text.strip()
    user_id = message.from_user.id
    logger.info('User %d spread: %s', user_id, user_request)

    thinking_msg = await message.answer(
        'Карты открываются...\nОракул читает твою судьбу...'
    )

    prompt = (
        f'Сделай расклад таро по запросу: "{user_request}". '
        'Выбери подходящие карты Старших и Младших Арканов, '
        'опиши каждую карту подробно с её символизмом и значением в данной позиции, '
        'дай общий вывод и практический совет.'
    )

    try:
        result = await generate_tarot_gigachat(prompt)
        await thinking_msg.delete()

        divider = '\n' + chr(9473) * 30 + '\n'
        header = f'РАСКЛАД ТАРО{divider}'
        footer = f'{divider}Напиши /tarot для нового расклада'
        full_text = header + result + footer

        max_len = 4096
        if len(full_text) <= max_len:
            await message.answer(full_text)
        else:
            chunks, current = [], ''
            for line in full_text.split('\n'):
                if len(current) + len(line) + 1 > max_len:
                    chunks.append(current)
                    current = line + '\n'
                else:
                    current += line + '\n'
            if current:
                chunks.append(current)
            for chunk in chunks:
                await message.answer(chunk)

        logger.info('Tarot sent to user %d', user_id)

    except httpx.HTTPStatusError as e:
        logger.error('HTTP error: %s', e)
        await thinking_msg.edit_text(
            f'Ошибка соединения с оракулом. Код: {e.response.status_code}\n'
            'Проверь GIGACHAT_AUTH_KEY в .env и попробуй снова.'
        )
    except Exception as e:
        logger.error('Error: %s', e)
        await thinking_msg.edit_text(
            f'Оракул временно недоступен.\nОшибка: {e}\nПопробуй /tarot снова.'
        )


@dp.message(F.text)
async def fallback_handler(message: Message):
    await message.answer('Напиши /tarot чтобы начать гадание\nили /help для справки')


async def main():
    logger.info('=== Tarot Bot starting ===')
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == '__main__':
    asyncio.run(main())
