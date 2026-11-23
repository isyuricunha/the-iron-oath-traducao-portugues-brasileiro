# Glossário PT-BR de Referência — *The Iron Oath*

> Este glossário consolida nomes próprios, facções, termos recorrentes já consolidados na interface migrada e pontos ainda em aberto para decisão editorial da tradução. Ele **não** é imposto por scripts automáticos — atualize este arquivo manualmente sempre que fechar ou revisar uma escolha terminológica.

## 1. Política geral de nomes próprios

| Categoria | Política sugerida | Observações |
| :--- | :--- | :--- |
| **Nomes pessoais de campanha** (`Euric`, `Vahn`, `Torin Bloodhand`, `Gedrick`, `Daemond`, `Innos`, `Nyra`, `Owayn`, `Hewford`) | Preservar (`Bloodhand` a decidir entre preservar ou adaptar como cognome) | Conferir consistência entre diálogos Yarn, UI I2 e Códice |
| **Continente e regiões** (`Caelum`, `Midland`, `Northern Hold`, `Westmire`, `Redlands`, `Dragate`, `Sundered Hollows`, `The Wastes`) | Preservar *Caelum* e nomes próprios; decidir regiões descritivas (*Redlands* / *Northern Hold* / *Westmire*) | Usar a mesma convenção em `Codex/codex.xml`, `cities.json` e `TioQuests.yarn.txt` |
| **16 Cidades** (`Andilon`, `Solace`, `Tyr`, `Haestenhold`, `Jorhurst`, `Raynelheim`, `Wealden`, `Korunhal`, `Shepfeld`, `Graveswood`, `Eyros`, `Kashaat`, `Athirton`, `Turrenhold`, `Terham`, `Talayse`) | Preservar integralmente (exceto possível escolha editorial para *Solace*) | Evitar alternar grafias entre mapas e eventos |
| **Casas nobres** (`Volaros`, `Jorsten`, `Athira`, `Talyron`, `Frosborn`, `Harthvalr`, `Stoneshield`, `Felsgrave`, `Kathir`, `Turren`, etc.) | Preservar sobrenomes; traduzir o título **`House`** como **`Casa`** (ou manter forma fixa escolhida) | Manter concordância consistente com nomes de casas no Códice |

## 2. Religiões, entidades e ameaças

| Original (EN) | Ocorrência / Uso | Sugestão / Estado atual |
| :--- | :--- | :--- |
| `Vismyr` / `Vismyr the Allmaker` | Divindade e religião nurren | `Vismyr` / decidir epiteto (`Vismyr, o Criador de Tudo` ou similar) |
| `Ascended Faith` / `Ascended` | Religião principal ligada a Tian | `Fé dos Ascendidos` / `Ascendidos` |
| `Elder Gods` / `Elder Faith` | Religião antiga (Westmire/Midden) | `Deuses Antigos` / `Fé Antiga` |
| `Divines` | Religião tyrosiana | `Divinos` |
| `Faith of the Dragon` | Culto em Dragate | `Fé do Dragão` |
| `Scourge` / `Risen Scourge` | Ameaça cíclica / horda | Decidir entre `Flagelo` (recomendado no contexto sombrio) ou `Praga` — evitar colidir com `Blight` |
| `Blight` / `Blighted` | Corrupção do Vazio que infecta terras e seres | Distinguir claramente de `Plague` e `Scourge` (ex.: `Miasma`/`Praga` vs. `Flagelo`) |
| `Void` / `Void Breach` / `Voidspawn` | Fendas dimensionais e monstros do Vazio | `Vazio`, `Brecha do Vazio` (ou `Fenda do Vazio`), `Cria do Vazio` (ou preservar `Voidspawn`) |
| `Vanguard Order` | Antiga ordem militar unificadora | `Ordem da Vanguarda` (ou `Ordem de Vanguarda`) |
| `Black Skull` | Companhia/facção antagonista | `Crânio Negro` / `Black Skull` (manter escolha uniforme em eventos e combates) |

## 3. Termos de combate e interface já consolidados em `I2` / `DLL`

Baseados nas traduções acentuadas já presentes nos lotes migrados de `interface-translations`:

| Termo EN | Forma em PT-BR já usada nos menus |
| :--- | :--- |
| `Accuracy` (`ACC`) | `Precisão` |
| `Attack Power` (`ATK`) | `Poder` |
| `Defense` | `Defesa` |
| `Evasion` | `Evasão` |
| `Crit Chance` / `Crit Dmg` | `Crítico%` / `Dano de Crítico` |
| `Health` | `Vida` |
| `Speed` | `Velocidade` |
| `Movement` | `Movimento` |
| `Spirit` / `Morale` | `Moral` |
| `Constitution` | `Vigor` |
| `Charisma` | `Convicção` |
| `Intelligence` | `Mente` |
| `Wisdom` | `Perspicácia` |
| `Strength` | `Físico` |
| `Dexterity` | `Destreza` |
| `Perception` | `Percepção` |
| `Mending` | `Cura` |
| `Company` | `Companhia` |
| `Dungeon` | `Calabouço` (ou padronizar `Calabouço` / `Masmorra` conforme preferência na revisão final) |
| `Loyalty` | `Lealdade` |
| `Fatigue` | `Fadiga` |
| `Aging Penalties` | `Penalidades de Envelhecimento` |

## 4. Itens pendentes para decisão editorial no Códice e dados novos

- **8 Classes recrutáveis:** decidir na revisão do Códice e `Abilities` quais permanecem traduzidas (*Guardião*, *Caçadora*, *Pugilista*, *Pirolanceiro*, *Baladeiro*, *Invocador de Tempestades*, *Vinculador de Gelo*) e como tratar `Valkyrie` (*Valkyrie* vs. *Valquíria*).
- **Nomes de itens de espólio (`Items/Loot/*.json`):** vários rótulos simples recém-expostos (`Buckler`, `Rotella`, `Brigandine`, `Gamebeson`, `Ale`, `Brandy`, etc.) permanecem vazios (`PTBR: ""`) para padronização manual no mesmo estilo dos equipamentos já traduzidos.
- **Códice completo (`Codex/codex.xml`, 876 entradas):** todo o acervo do Códice foi preservado intacto com `PTBR: ""` para tradução manual coerente com a terminologia fechada acima.
