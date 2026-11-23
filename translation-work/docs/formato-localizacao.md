# Formato de Localização e Engenharia Nativa — *The Iron Oath*

## 1. Inventário de formatos extraídos (`EN/` v1.0.021)

O kit de ferramentas extrai e recompila **7 formatos nativos**, totalizando **15.794 unidades únicas** organizadas em **32 lotes** (`500` unidades por lote) em `translation-work/translations/`:

| Formato | Motor / Handler | Quantidade | Arquivos / Localização nativa | Observações de ida e volta |
| :--- | :--- | :---: | :--- | :--- |
| `yarn` | `formats/yarn_format.py` | 9.443 | `StreamingAssets/Data/QuestData/*.yarn.txt` | Preserva comentários, comandos lógicos e `<<...>>`; extrai falas, opções `->[...]`, comandos narrativos permitidos e `[Action:...]` |
| `i2_mono` | `formats/i2_mono_format.py` | 2.211 | `resources.assets` (`I2Languages` MonoBehaviour, pid `532`) | Substitui o idioma `English` mantendo termos, tipos e índices exatamente alinhados em binário Unity v21 |
| `dll` | `formats/dll_format.py` + `DllStringPatch.dll` | 1.519 | `Managed/Assembly-CSharp.dll` (396 tipos sob `source-policy.json`) | Patch de literais `ldstr` em IL via Mono.Cecil; `check` bloqueia strings usadas como enum/comparação lógica ou Lookup |
| `hjson` | `formats/hjson_format.py` | 1.510 | 267 arquivos sob `StreamingAssets/Data/` | Preserva integralmente comentários, indentação, vírgulas opcionais e ordem das chaves via reescrita por span exato |
| `codex_xml` | `formats/codex_xml_format.py` | 876 | `StreamingAssets/Data/Codex/codex.xml` | Atributos `name` (exceto `traitId`), `<description>`, `<ability>` e `<passive>`; preserva `<id>`, `<template>`, `|NEXTPAGE|` e comentários |
| `scene_mono` | `formats/scene_mono_format.py` | 199 | `level0`–`level6`, `sharedassets0.assets`–`sharedassets6.assets` | Strings deduplicadas de componentes TextMeshPro / MonoBehaviours sob IDs de script verificados (`TMP_TEXT_SCRIPT` e painéis UI) |
| `textasset_xml` | `formats/textasset_xml_format.py` | 36 | `resources.assets` (`LocalizationText` TextAsset, pid `96`) | XML embutido em `TextAsset` mantendo nós `<EN>` com alinhamento de 4 bytes |

## 2. Anatomia do binário Unity v21 (`formats/unity_assets.py`)

Todos os 15 arquivos de assets do jogo (`level0`–`level6`, `sharedassets0.assets`–`sharedassets6.assets`, `resources.assets`) usam `SerializedFile` **formato 21** (*little-endian*), **sem compressão**, com Unity `2019.4.20f1`.
O parser/reconstrutor puro em Python recalcula deslocamentos, comprimentos de página e alinhamentos de **4 bytes** sem depender de módulos nativos em C/C++ (evitando a ausência de wheels ARM64 para pacotes externos).

## 3. Auditoria de fontes TextMeshPro

Durante o levantamento técnico dos assets (`resources.assets` / `sharedassets*.assets`), os seguintes atlas de fonte foram observados:

- `IronOathText` (pid `1119`), `IronOathTextStroke` (pid `1120`) e `IronOathTextStrokeUnderline` (pid `2713`): **88 glifos** (ASCII padrão, **0** acentos PT-BR).
- `IronOathTextAlt` (pid `1837`) e `IronOathTextCustom` (pid `1838`): **98 glifos** (**0** acentos PT-BR).
- `dum1 SDF` (pid `2714`): **97 glifos** (**0** acentos PT-BR).
- `LiberationSans SDF` (pid `1842`): **250 glifos** (**24** diacríticos/letras PT-BR).

Como quase todo o jogo renderiza diálogos, habilidades e botões usando a família `IronOathText*`, `engine-shapes.json` define `"strip_diacritics_on_build": true`. Durante `build`, caracteres acentuados do texto mestre em JSONL são convertidos para equivalentes ASCII legíveis (`ç` → `c`, `ã` → `a`, etc.).

## 4. Políticas e recebimento em camadas

- `translation-work/engine-shapes.json`: define versão de referência (`1.0.021`), codificações, delimitadores e caminhos canônicos de entrada/saída.
- `translation-work/source-policy.json`: trava os **396 tipos** de `dll_scope` (1.519 literais seguros), as **120** strings ignoradas em `scene_ignore` (créditos internos/placeholders) e os **26** termos técnicos ignorados em `i2_skip_terms`.
- `translation-work/toolchain.json`: registra hashes SHA-256 canônicos (`git-lf` para texto e `binary` para binários) em cadeia controlada para impedir adulteração silenciosa ou falha falsa entre Windows CRLF e Linux LF.
- `translation-work/runtime-support.json`: valida o hash de `toolchain.json` e documenta requisitos de execução em Windows e Ubuntu ARM64.
