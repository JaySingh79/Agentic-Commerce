# Project README Template

Brief introduction to the project, its core value proposition, and quick links.

## Table of Contents
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Usage](#usage)
- [AI Workflow](#ai-workflow)
- [Deployment](#deployment)

## Prerequisites
- List any software required, such as:
  - [Node.js](https://nodejs.org/) (v18+)
  - [Python](https://www.python.org/) (v3.10+)
  - [Git](https://git-scm.com/)

## Installation
How to set up the local development environment:
```bash
# Clone the repository
git clone https://github.com/user/repo-name.git
cd repo-name

# Install dependencies (choose the one applicable to your stack)
npm install
# OR
pip install -r requirements.txt
```

## Usage
How to run scripts, start servers, or execute tests:
```bash
# Run local dev server
npm run dev

# Run test suite
npm run test
```

## AI Workflow
This project utilizes the **Global Skill and Memory Layer**.
- Global memory points to: `memory.md` (which maps back to the master global memory).
- Global skills point to: `skills.md` (which maps back to the master global skills).
- Project-specific configuration is isolated in:
  - [project_context.md](file:///D:/AI/global/templates/project_context.md) (static facts)
  - [project_memory.md](file:///D:/AI/global/templates/project_memory.md) (dynamic conventions and learnings)
