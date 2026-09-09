# GetPostach — кабінет закупівельника (visionOS)

Статичний фронтенд особистого кабінету закупівельника Alliance Group 95: дашборд,
постачальники, RFQ-розсилки, моніторинг цін, якість і переговори, помічник «Пігулькін».

**Live:** https://artyd.github.io/GetPostach/

## Як це працює

Застосунок повністю клієнтський. `index.html` містить шаблон і логіку; `support.js`
(DC-runtime) підвантажує React/Babel з CDN і рендерить сторінку в браузері. Дані
читаються синхронно зі статичних файлів через глобальні змінні:

| Файл | Глобальна змінна | Що це |
|---|---|---|
| `suppliers-data.js` | `window.GP_SUPPLIERS` | список постачальників (рейтинг, контакти, позиції та історія цін) |
| `data/site-data.js` | `window.GP_SITE` | повна база компаній у діалозі (контакти, резюме, ціни) |
| `data/product-i18n.js` | `window.GP_PROD_I18N` | довідник субстанцій (укр. назва + CAS) |

Окремий сервер не потрібен — це статичний сайт (GitHub Pages).

## Джерело даних (бекенд)

`suppliers-data.js` і `data/site-data.js` генеруються з бази
[CPHI_MILAN](https://github.com/artyd/CPHI_MILAN) скриптом `build-data.js`:

- `data/site-data.js` (`GP_SITE`) — з об'єднаного `DATA` у `CPHI_MILAN/index.html`
  (компанії + контактні особи + резюме + згруповані ціни);
- `suppliers-data.js` (`GP_SUPPLIERS`) — з `CPHI_MILAN/data/ranking.jsonl`
  (score/статус/метрики), доповнений контактами, історією цін і стендами CPHI з `DATA`.

Косметичні поля (`mono`, `rating`, `cat`) обчислюються евристично; усе змістовне —
з реальної бази.

### Оновити дані

```bash
node build-data.js   # перечитує ../CPHI_MILAN і перезаписує data-файли
git add -A && git commit -m "refresh data" && git push
```

> Скрипт очікує репозиторій `CPHI_MILAN` поруч: `C:/Projects/Артем/CPHI_MILAN`.
