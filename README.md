<p align="center">
  <img src="assets/readme.gif" width="100%" alt="MAIC-UI demo"/>
</p>

<h1 align="center">MAIC-UI</h1>
<p align="center"><strong>AI-Powered Interactive Course Generation System</strong></p>
<p align="center">From MOOC to MAIC: Reimagine Online Teaching and Learning through LLM-driven Agents</p>

---

## What is MAIC-UI?

MAIC-UI is an AI-powered interactive teaching generation system. It transforms PDFs, PowerPoint slides, and concept descriptions into rich, interactive HTML learning courses — complete with exercises, simulations, and guided exploration.

> **Let knowledge grow into interfaces, and let interaction flow into thinking.**

Unlike static courseware, MAIC-UI generates both **content** and **learning process** — students don't just read knowledge, they manipulate, experience, and understand it.

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Git

### 1. Clone

```bash
git clone https://github.com/LJHSTO/MAIC-UI.git
cd MAIC-UI
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env and add your SiliconFlow API key:
#   TRANSFER_API_KEY=sk-...
```

### 3. Deploy

```bash
docker compose build
docker compose up -d
```

Open **http://localhost:8927** in your browser.

| Service | Port | Description |
|---------|------|-------------|
| nginx | 8927 | Reverse proxy (public entry) |
| frontend | 3000 | Next.js application |
| backend | 8000 | FastAPI application |

## Development Mode

```bash
npm run install:all   # Install all dependencies
npm run dev           # Start frontend (3000) + backend (8000)
```

## Features

###  Use Cases

| Scenario | Description |
|----------|-------------|
| **Lesson Introduction** | Attract students' attention with intuitive interactive pages |
| **Knowledge Explanation** | Transform abstract concepts into visual + interactive content |
| **Experiment Simulation** | Demonstrate processes when lab equipment is limited |
| **After-Class Consolidation** | Strengthen understanding through interactive exercises |

![Lesson Introduction](assets/Lesson_Introduction.PNG)
![Knowledge Explanation](assets/Knowledge_explanation.PNG)
![Experiment Simulation](assets/Experiment_simulation.PNG)
![Consolidation](assets/consolidation.PNG)

### Advantages

| Dimension | Traditional Courseware | **MAIC-UI** |
|:--|:--|:--|
| Production threshold | High, manual design | **Low, AI-generated** |
| Content form | Static presentation | **Dynamic + interactive** |
| Student role | Passive viewer | **Active participant** |
| Abstract knowledge | Hard to express | **Visual + manipulable** |
| Teaching adaptability | High adjustment cost | **Quick regeneration** |

##  Batch Course Generation (CLI)

Generate courses in bulk without opening the browser:

```cmd
# Windows (cmd / PowerShell)
1. copy .env.batch.example .env.batch  →  edit MAIC_API_BASE
2. maic-gen setup                       →  register & verify
3. maic-gen concept examples\\concept_derivative.json
4. maic-batch examples\\batch_courses.tsv
```

```bash
# Linux / macOS / Git Bash
MAIC_API_BASE=http://HOST_IP:8000/api \
  MAIC_EMAIL=user@example.com \
  MAIC_PASSWORD='password' \
  ./scripts/maic_generate_course.sh concept examples/concept_derivative.json

./scripts/maic_batch_generate.sh examples/batch_courses.tsv
```

See [docs/MAIC_BATCH_GENERATION.md](docs/MAIC_BATCH_GENERATION.md) for full documentation.

## Project Structure

```
MAIC-UI/
├── frontend/                     # Next.js + React + TypeScript
│   ├── src/
│   │   ├── app/                  # Page routes
│   │   ├── components/           # Shared components
│   │   │   ├── providers/        # LanguageProvider, ModelSettingsProvider
│   │   │   ├── pdf/              # PDF upload & viewer components
│   │   │   ├── ppt-viewer/       # PPT upload & viewer components
│   │   │   └── WebEditor/        # Rich course editor
│   │   └── services/             # API client layer
│   └── package.json
├── backend/                      # FastAPI + SQLAlchemy
│   ├── src/
│   │   ├── api/                  # Route handlers
│   │   ├── services/             # AI processing, personalization, generation
│   │   │   └── html_generation/  # Heavy & fast HTML generators
│   │   ├── models/               # SQLAlchemy ORM models
│   │   └── core/                 # Config, database, security
│   └── main.py
├── scripts/                      # Batch generation CLI
│   ├── maic_generate_course.sh   # Single course (bash)
│   ├── maic_batch_generate.sh    # Batch dispatcher (bash)
│   ├── maic_generate.ps1         # Single course (PowerShell)
│   ├── maic_batch.ps1            # Batch dispatcher (PowerShell)
│   └── maic_share_backend.sh     # Expose backend to LAN
├── examples/                     # Batch manifest examples
├── docs/                         # Documentation
├── docker-compose.yml
└── README.md
```

##  Supported AI Models

MAIC-UI supports 20+ models via a unified SiliconFlow transfer station:

| Provider | Models |
|----------|--------|
| **Zhipu** | GLM-4.7, GLM-4.6, GLM-5.1 |
| **Anthropic** | Claude Opus 4.6/4.7, Sonnet 4.5/4.6, Haiku 4.5 |
| **OpenAI** | GPT-5, GPT-5.4, GPT-5.5, GPT-4.1 Mini |
| **DeepSeek** | V4 Pro (1M context), V4 Flash |
| **Google** | Gemini 3.1 Pro, 3.5 Flash, 3 Flash, 2.5 Pro |
| **Qwen** | Qwen3.6 35B A3B, Qwen Plus, Max, Turbo |
| **Moonshot** | Kimi K2.6 |
| **MiniMax** | MiniMax M2.5 |

## Core Architecture

The system operates around this pipeline:

```
PDF/PPT/Concept Input  →  AI Content Generation  →  Interactive Page Building  →  Learning Delivery
```

- **Frontend layer**: User interaction, page presentation, resource display
- **Backend layer**: Business logic, API management, generation workflow scheduling
- **AI generation layer**: Content generation, page organization, interactive resource construction
- **Data layer**: User profiles, resource configuration, generation results

## Contributing

We welcome bug reports, feature suggestions, and pull requests.

## Business Cooperation

For educational products, learning platforms, course resource development, or institutional partnerships:

- **Email**: tsq25@mails.tsinghua.edu.cn

## Citation

```bibtex
@Article{JCST-2509-16000,
  title     = {From MOOC to MAIC: Reimagine Online Teaching and Learning through LLM-driven Agents},
  journal   = {Journal of Computer Science and Technology},
  year      = {2026},
  doi       = {10.1007/s11390-025-6000-0},
  url       = {https://jcst.ict.ac.cn/en/article/doi/10.1007/s11390-025-6000-0},
  author    = {Ji-Fan Yu and Daniel Zhang-Li and Zhe-Yuan Zhang and Yu-Cheng Wang and Hao-Xuan Li
               and Joy Jia Yin Lim and Zhan-Xin Hao and Shang-Qing Tu and Lu Zhang and Xu-Sheng Dai
               and Jian-Xiao Jiang and Shen Yang and Fei Qin and Ze-Kun Li and Xin Cong and Bin Xu
               and Lei Hou and Man-Li Li and Juan-Zi Li and Hui-Qin Liu and Yu Zhang
               and Zhi-Yuan Liu and Mao-Song Sun}
}
```

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=LJHSTO/MAIC-UI&type=Date)](https://star-history.com/#LJHSTO/MAIC-UI&Date)
