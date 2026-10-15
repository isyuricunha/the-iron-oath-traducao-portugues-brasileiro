# The Iron Oath - Traducao para Portugues (Brasil)

Traducao dos menus, da interface e dos textos do jogo para Portugues
(Brasil).

## Instalacao

1. Feche o jogo.
2. Copie o CONTEUDO desta pasta para a raiz da instalacao do jogo,
   mesclando a pasta `The Iron Oath_Data/` com a que ja existe.
   Nao copie a pasta `PTBR` em si.
3. A traducao inclui a DLL `The Iron Oath_Data/Managed/Assembly-CSharp.dll`
   (textos de interface que ficam no codigo). Ela substitui a original na
   instalacao; faca um backup da sua `Assembly-CSharp.dll` antes de colar,
   caso queira voltar atras.
4. PNGs, musicas e videos nao fazem parte da traducao: o jogo usa os que
   ja existem na sua instalacao.

## O que esta incluido

- Dialogos, itens, sistemas, mundo e dungeons (HJSON em `StreamingAssets`)
- A tabela I2Languages (`resources.assets`): menu, tooltips, habilidades,
  upgrades, vagas e contratos
- Textos de interface gravados nas cenas (`level*` / `sharedassets*`):
  menus principais, paineis, tela de game over, janela de feedback
- Textos de interface no codigo (`Assembly-CSharp.dll`): telas de
  dificuldade, mercado, mercador, contratos, taverna, enfermaria,
  estabulo, guilda e mais

## Sobre acentos

As fontes do jogo sao bitmaps sem glifos acentuados, entao a traducao e
distribuida sem acentos. Quando as fontes forem substituidas por versoes
com acentos, o pipeline publica a versao acentuada sem retrabalho.
