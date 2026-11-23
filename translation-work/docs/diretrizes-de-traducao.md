# Diretrizes de Tradução — *The Iron Oath* PT-BR

## 1. Registro, tom e clareza

- **Fantasia sombria medieval:** adote vocabulário sério, direto e evocativo condizente com uma companhia mercenária experiente. Evite gírias modernas, jargão corporativo ou tom humorístico fora das falas leves intencionais de taverna e acampamento.
- **Concisão na interface:** rótulos de botões, atributos, habilidades e `PlayerJobs` aparecem em painéis com largura fixa em tela. Quando uma tradução ficar muito mais longa que o inglês original, prefira formulações curtas e diretas sem mutilar palavras.
- **Naturalidade falada:** diálogos em `TioQuests.yarn.txt` e `Dialogue/DynamicDialogue/*.json` devem soar naturais em voz alta, mantendo pontuação expressiva e fluidez em português brasileiro.

## 2. Grafia nos lotes vs. texto exibido no jogo

- Nos lotes de trabalho (`translation-work/translations/batch-*.jsonl`), escreva **com acentuação padrão completa da língua portuguesa** (`ç`, `ã`, `á`, `é`, `ê`, `í`, `ó`, `ô`, `ú`, etc.).
- O comando `build` remove os diacríticos automaticamente ao gerar `PTBR/` para compatibilidade com as fontes embutidas do jogo (`IronOathText*`), enquanto `translation-work/translations/` preserva a versão acentuada como matriz oficial.
- Dica prática: ao traduzir falas onde a perda de acento possa confundir o jogador (por exemplo *"é"* vs. *"e"*, *"pôr"* vs. *"por"*), prefira estruturar a frase de modo que o sentido continue inequívoco mesmo sem acentos.
- Nota histórica da migração: cerca de três mil entradas herdadas de `PTBR/` antigo vieram sem acento nos lotes migrados (`check` lista esses avisos não bloqueantes como `unaccented_ptbr_hints`); reacentuar gradualmente essas linhas melhora a qualidade da matriz sempre que houver revisão.

## 3. Regras técnicas inegociáveis (tokens e delimitadores)

Cada entrada é validada automaticamente por `translation-work/syntax.py` antes de qualquer `apply` ou `build`:

1. **Preserve integralmente todos os marcadores entre colchetes e chaves:**
   - Exemplos: `[FNameP0]`, `[FNameC0]`, `[FNameAdvisor]`, `[myTitle]`, `[firstName]`, `[pronounSingular]`, `[pronounObjective]`, `[pronounPossessive]`, `[className]`, `{0}`, `{[PARAM1]}`, `{advisor}`, `{player:0}`, `{mainChar0}`.
   - Você pode reposicionar o token na frase para manter a ordem natural do português, mas **nunca** o traduza, apague, altere a caixa das letras ou duplique sem que exista no original.
2. **Preserve todas as tags rich-text de TextMeshPro:**
   - `<color=#...>`, `</color>`, `<b>`, `</b>`, `<i>`, `</i>`, `<s>`, `</s>`, `<sprite name="...">`, `<link=...>`.
   - Em fragmentos concatenados no código C# (`dll`), mantenha exatamente a mesma abertura ou fechamento parcial que já aparece no `EN` (por exemplo `Repair Cost: <color=red>` → `Custo do Reparo: <color=red>`).
3. **Preserve contagens de `|` e `^`:**
   - Em tooltips com múltiplas páginas, cada bloco separado por `|` corresponde a uma seção/cartão.
   - O caractere `^` separa o texto de **Mouse/Teclado** (antes de `^`) do texto de **Controle/Gamepad** (após `^`). Mantenha os dois lados correspondentes e exatamente o mesmo número de `^`.
4. **Preserve `|NEXTPAGE|` em `Codex/codex.xml`:**
   - O marcador `|NEXTPAGE|` divide páginas físicas da janela do Códice no jogo.
5. **Prefixos numéricos de falante (`3:Texto`, `1:Texto`):**
   - Em alguns diálogos HJSON (`questDialogue.json`, `randomDialogue.json`), o número antes dos dois-pontos (`3:`) identifica o retrato/slot do falante. Preserve o prefixo `N:` intacto antes da fala traduzida (ex.: `3:Mantenha a guarda alta!`).

## 4. Gênero gramatical e contexto dinâmico

Cada linha no JSONL inclui o campo `context` para ajudar a identificar quem fala, quem escuta e sobre quem se fala:

- **`speaker`, `speaker_gender`, `certainty`, `evidence`:** mostram se há evidência textual/estrutural de gênero (`confirmed`) ou apenas pista de função (`inferred_from_role`), evitando suposições cegas.
- **`dynamic_tokens` e `review_flags`:** sinalizam falas envolvendo o jogador (`[myTitle]`, `{player:0}`) ou recrutas gerados aleatoriamente (`<<CreateNpc ... Sex>>`, `backstories.json`), cujo gênero varia em tempo de execução. Nesses casos, prefira redação neutra/versátil em português (ex.: *"Consegue ver a saída?"*, *"Lidere o ataque"* em vez de forçar particípios ou adjetivos marcados sem necessidade).
- **Diálogos sequenciais (`neighbor_prev` / `neighbor_next`):** consulte as falas vizinhas já incluídas no registro antes de fechar a concordância de turnos curtos.
