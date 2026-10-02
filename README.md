# 🗺️ map-render

Всё, что рисует карту Rust-сервера в GitHub Actions.

```
map-render/
├── .github/workflows/main.yml   # полный рендер: Rust Dedicated Server + Oxide → world.rendermap
├── plugins/                     # Oxide-плагины, которые main.yml кладёт на сервер
│   ├── MapLabels.cs             # подписи монументов + rendermap → MapLabels.json
│   ├── LootSpawns.cs            # ящики, руда, точки спавна игроков → LootSpawns.json
│   └── MonumentFinder.cs        # сторонний плагин, API поиска монументов
├── fastmap/                     # быстрый рендер .map на Python без Rust-сервера (fast-map.yml)
│   ├── fastmap.py
│   └── requirements.txt
└── assets/fonts/                # шрифт подписей (Permanent Marker)
```

## Полный рендер (`main.yml`)

GitHub запускает workflow только из корневой папки `.github/`, поэтому полный рендер живёт в **отдельном
репозитории** (`GITHUB_REPO` у бэкенда). Содержимое этой папки копируется в **корень** того репозитория как есть:
`main.yml` ищет плагины в `plugins/`, а шрифт — в `assets/fonts/`.

## Быстрый рендер (`fastmap/`)

Запускается workflow `.github/workflows/fast-map.yml` из этого репозитория (`GITHUB_FAST_REPO`).

```bash
pip install -r map-render/fastmap/requirements.txt
python map-render/fastmap/fastmap.py map.map out/ \
  --labels-cs map-render/plugins/MapLabels.cs \
  --font map-render/assets/fonts/permanent-marker-v16-latin-regular.ttf
```
