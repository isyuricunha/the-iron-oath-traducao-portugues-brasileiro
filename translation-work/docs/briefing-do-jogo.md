# Briefing do Jogo — *The Iron Oath* (Referência para Tradução PT-BR)

> Este documento reúne contexto factual do jogo para orientar decisões linguísticas e terminológicas durante a tradução manual em português brasileiro. Todas as escolhas editoriais de vocabulário, tratamento e estilo cabem à tradução.

## 1. Identidade e referências oficiais

- **Título:** *The Iron Oath* (GOG `1.0.021` / Steam AppID `699330`)
- **Desenvolvimento:** Curious Panda Games
- **Publicação:** Balor Games (edição de lançamento originalmente publicada com participação da Humble Games, preservada em créditos internos em `EN/The Iron Oath_Data/level5`)
- **Lançamento 1.0:** 2 de novembro de 2023 (`PatchNotes.txt` atualizado até a versão `1.0.021` de 18 de dezembro)
- **Motor:** Unity `2019.4.20f1` (backend Mono/.NET)
- **Gênero e tom:** RPG tático por turnos de fantasia sombria com gestão de companhia mercenária, passagem real de anos de campanha (envelhecimento, lealdade, ferimentos, aposentadoria) e simulação dinâmica de mundo.
- **Páginas e documentação primária:**
  - Página oficial na Steam: <https://store.steampowered.com/app/699330/The_Iron_Oath/>
  - Página oficial na GOG: <https://www.gog.com/en/game/the_iron_oath>
  - Página da publicadora Balor Games: <https://balorgames.com/games/iron-oath>
  - Notas de atualização no acervo: `EN/The Iron Oath_Data/StreamingAssets/Data/PatchNotes.txt`
  - Acervo narrativo e mundial no acervo: `EN/The Iron Oath_Data/StreamingAssets/Data/WorldSim/` e `EN/The Iron Oath_Data/StreamingAssets/Data/Codex/codex.xml`

---

## 2. O mundo de Caelum: geografia, história e forças em conflito

### 2.1 Cinco regiões e dezesseis cidades

Segundo `EN/The Iron Oath_Data/StreamingAssets/Data/WorldSim/Cities/cities.json` e `WorldSim/codex.json`, o continente/reino de **Caelum** se divide em **cinco regiões principais** e **16 cidades**:

| Região | Cidades confirmadas nos arquivos | Notas de contexto |
| :--- | :--- | :--- |
| **Midland** | **Solace** (capital), **Andilon**, **Terham**, **Talayse** | Coração comercial e eclesiástico; Andilon serve de refúgio inicial pós-traição |
| **Northern Hold** | **Haestenhold**, **Jorhurst**, **Raynelheim**, **Wealden** | Norte frio; sede histórica da *Vanguard Order* |
| **Westmire** | **Korunhal**, **Shepfeld**, **Graveswood** | Terras pantanosas e tradição ligada aos *Elder Gods* |
| **Redlands** | **Tyr** (capital), **Eyros**, **Kashaat** | Região árida governada por linhagens tyrosianas (*House Volaros* / Rei *Nasos Volaros*) |
| **Dragate** | **Athirton**, **Turrenhold** | Fronteira associada à *Black Wall of Dragate*, *Sundered Hollows* e *The Wastes* |

### 2.2 Cronologia histórica (`WorldSim/codex.json` e `Codex/codex.xml`)

1. **First Age** e **Age of Gods:** eras míticas anteriores à unificação humana.
2. **Dark Age:** guerras antigas em que o Rei **Tian** unifica Caelum e funda a **Vanguard Order**; o chefe nurren **Hyldr** ascende ao plano **Hvillholl**.
3. **Common Age:** difusão da *Common Script*, escritos de **Lorrec** sobre Tian e consolidação da **Ascended Faith**.
4. **Red Plague** e **Wars of Dissolution:** pestilência e guerras civis que fragmentam o poder central entre casas nobres.
5. **Presente do jogo:** ciclos recorrentes do **Scourge** (o dragão primordial e as hordas de **Voidspawn** que emergem pelas **Void Breaches**), disseminando a corrupção **Blight** (**Blighted**) sobre cidades, ruínas e companhias mercenárias.

### 2.3 Casas nobres, ordens e facções (`WorldSim/houses.json`, `TioQuests.yarn.txt`)

- **Ordens e grupos relevantes:** **Vanguard Order** (liderada por **Commander Owayn**, com presença do **Captain Hewford** em Andilon), **Circle of Aether** (arcanistas), **Overseers** e a força antagonista/mercenária **Black Skull** (chefiada por **Torin Bloodhand**).
- **Casas nobres:** *Jorsten*, *Athira*, *Talyron*, *Volaros*, *Frosborn*, *Harthvalr*, *Stoneshield*, *Felsgrave*, *Kathir*, *Turren* e demais casas listadas em `WorldSim/houses.json`.

### 2.4 Religiões e altares (`WorldSim/religions.json`, `Systems/Shrines.json`)

- **Cinco tradições religiosas de mundo:** **Ascended Faith** (culto ligado a Tian e ao arquiprelado em Solace), **Elder Gods / Elder Faith** (Midden/Westmire), **Vismyr the Allmaker** (tradição nurren), **Divines** (culto tyrosiano aos reis ancestrais) e **Faith of the Dragon** (cultistas em Dragate).
- **Altares de masmorra (`Systems/Shrines.json`):** *Peras* (Fortitude), *Haldrin* (Creation), *Dyaena* (Mercy), *Hyldr* (Warrior), *Thera* (Protection), *Phaetes* (Destiny), *Haera* (Prosperity), *Teral* (Land), *Ceres* (Harvest), *Osymus* (Greed), *Tychros* (Time), além de *Vismyr*, *Anusa* e provações de *Tian*.

---

## 3. Campanha principal e personagens

### 3.1 Prólogo e traição (`QuestData/TioQuests.yarn.txt` e `Dialogue/DynamicDialogue/questDialogue.json`)

- Em `SE.CampaignPrologueIntro`, a companhia entra em uma ruína para recuperar suprimentos e treinar o comando tático básico junto de **Euric** (`{mainChar0}` / `[FNameC0]`), **Vahn** (`{mainChar1}` / `[FNameC1]`), do **Conselheiro** da companhia (`{advisor}` / `[FNameAdvisor]`) e dos aliados iniciais **Daemond**, **Innos**, **Nyra** e **Torin Bloodhand** (`{mainAnt0}` / `[FNameA0]`).
- Em `SE.CampaignBetrayalCombatStart`, **Torin** e parte dos desertores emboscam e traem a companhia no acampamento. O(a) protagonista comandante, **Euric** e o **Conselheiro** (ferido) fogem para **Andilon**.
- Ao longo da campanha principal (com prazo configurável de 30 anos de jogo), a companhia se reconstrói, busca o paradeiro de **Gedrick** (`{npc:Gedrick}`, sobrinho de Euric) e trilha o arco de vingança contra **Torin** (*Path of Vengeance*).

### 3.2 Gênero confirmado de personagens vs. tokens dinâmicos

| Identificador nos textos | Entidade | Evidência nos arquivos | Orientação de concordância |
| :--- | :--- | :--- | :--- |
| `{player:0}`, `[myTitle]`, `[FNameP0]` | Comandante (jogador) | Gênero escolhido ao iniciar a campanha | Use linguagem neutra sempre que possível ou acompanhe os marcadores de pronome quando presentes; não fixe masculino sem confirmação do contexto |
| `{mainChar0}`, `[FNameC0]` | **Euric** | Pronomes `he`/`him` em `TioQuests.yarn.txt` | **Masculino confirmado** |
| `{mainChar1}`, `[FNameC1]` | **Vahn** | Pronomes `he`/`him` em diálogos de aposentadoria e prólogo | **Masculino confirmado** |
| `{advisor}`, `[FNameAdvisor]` | **Conselheiro** (`Advisor`) | Referências `he`/`him` e cargo masculino nos textos da campanha | **Masculino confirmado** |
| `{mainAnt0}`, `[FNameA0]` | **Torin Bloodhand** | Pronomes `he`/`him` e antagonista central | **Masculino confirmado** |
| `{npc:Gedrick}` | **Gedrick** (sobrinho de Euric) | `"nephew"` e pronomes masculinos em `TioQuests.yarn.txt` | **Masculino confirmado** |
| `<<CreateNpc ... Sex>>`, `{player:$var}`, `[FNameP0..3]` | Recrutas e figurantes dinâmicos | `Sex` aleatório ou gerado em tempo de execução (`backstories.json`, eventos aleatórios) | Preserve `[pronounSingular]`, `[pronounObjective]`, `[pronounPossessive]`, etc.; verifique os metadados `context.gender_hints` de cada linha |

---

## 4. Mecânicas, classes e ocupações

### 4.1 Oito classes recrutáveis padrão (v1.0)

Extraídas de `EN/The Iron Oath_Data/StreamingAssets/Data/Actors/CharacterClasses/` e `I2Languages`:

- **Balladeer** (suporte musical / inspirou composições e controle de campo)
- **Guardian** (linha de frente com escudo, defesa e controle de zona)
- **Huntress** (classe de arco à distância; referenciada no feminino em habilidades/contextos)
- **Icebinder** (conjurador de gelo e controle)
- **Pugilist** (combate corpo a corpo, empurrões e atordoamentos)
- **Pyrolancer** (conjurador de fogo e armadilhas de área)
- **Stormcaller** (mago de raios e habilidades canalizadas)
- **Valkyrie** (classe marcial sagrada/lâmina; gênero feminino na tradição da classe)

Também existem definições de aliados/npcs específicos de campanha (`Woodsman`, `Bloodhound`, `Adventurer`, `Daemond`, `Innos`, `Torin Bloodhand`).

### 4.2 Ocupações da companhia (`Systems/PlayerJobs.json`)

Em `PlayerJobs.json`, o campo `Job` funciona como enum/chave lógica (nunca alterada pela extração), enquanto `Name` é exibido ao jogador em menus e eventos de acampamento/cidade. Mantenha traduções concisas que caibam nos cartões da interface.

---

## 5. Desafios práticos na tradução PT-BR deste jogo

1. **Fontes embutidas e ausência de acentos na compilação (`build`):**
   - Auditoria nos assets da Unity (`sharedassets*.assets`, `resources.assets`) mostrou que `IronOathText`, `IronOathTextStroke` e `IronOathTextStrokeUnderline` possuem 88 glifos sem letras acentuadas em português, `IronOathTextAlt/Custom` possuem 98 glifos sem acentos PT-BR e `dum1 SDF` possui 97 glifos sem acentos PT-BR; apenas `LiberationSans SDF` possui cobertura completa (250 glifos).
   - **Regra do fluxo de trabalho:** os lotes em `translation-work/translations/batch-*.jsonl` devem usar **português acentuado padrão**, enquanto `build` remove diacríticos automaticamente ao compilar `PTBR/` para evitar quadrados faltantes no jogo. Evite abreviar palavras de forma que percam clareza quando estiverem sem acento (ex.: prefira construções onde *"é"* / *"e"* ou *"está"* / *"esta"* não gerem ambiguidade séria ao leitor sem o acento).
2. **Delimitadores `|` e `^` em tooltips e HJSON:**
   - Em HJSON (`ToolTip`), `|` separa múltiplas páginas/cartões de um mesmo tooltip e `^` separa variantes **Teclado/Mouse vs. Controle** (ex.: `PC-leftClick` antes de `^` e `XboxOne-a` depois de `^`). A contagem exata de `|` e `^` é obrigatória em `validate_translation`.
3. **Tags `[Action:...]` e linhas `[texto]` em Yarn:**
   - Tokens estruturais como `[Locked:keyOrTools]` ou `[Mining:minerToolsBlast]` são palavras-chave do motor e não entram como tradução livre, ao passo que `[Action:Continue]`, `[Action:Exit]` e linhas inteiras descritivas entre colchetes (`[Use the scroll]`) são extraídas de forma limpa sem os colchetes externos. O parser recoloca a moldura `[Action:...]` / `[...]` na compilação.
4. **Quebras de página em `Codex/codex.xml`:**
   - Entradas de diário e lore usam `|NEXTPAGE|` entre páginas de leitura do Códice. Mantenha o token `|NEXTPAGE|` exatamente igual e respeite o limite aproximado de tamanho de cada página para não exigir rolagem excessiva ou vazar da moldura.
5. **Strings fragmentadas em `Assembly-CSharp.dll`:**
   - Algumas telas montam frases concatenando literais em C# (por exemplo `"Repair Cost: <color=red>"` seguido de um número e `"</color>"`). Leia `context.il_usages` e preserve espaços finais, dois-pontos e tags parciais exatamente como aparecem no original `EN`.
