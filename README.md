<div align="center">
  <a href="https://store.steampowered.com/app/699330/The_Iron_Oath/"><img src="https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/699330/header.jpg" alt="Arte oficial de The Iron Oath" width="650"></a>
  <h1>The Iron Oath</h1>
  <p><strong>🇧🇷 Tradução para Português Brasileiro</strong></p>
  <p><em>Entre juramentos de ferro e o avanço do Vazio, toda companhia paga o preço do tempo.</em></p>
  <p>
    <a href="https://store.steampowered.com/app/699330/The_Iron_Oath/"><img src="https://img.shields.io/badge/Steam-The_Iron_Oath-1b2838?style=for-the-badge" alt="Steam — página do jogo"></a>
    <a href="https://www.gog.com/en/game/the_iron_oath"><img src="https://img.shields.io/badge/GOG-The_Iron_Oath-5c2d91?style=for-the-badge" alt="GOG — página do jogo"></a>
    <a href="https://github.com/isyuricunha/the-iron-oath-traducao-portugues-brasileiro/releases"><img src="https://img.shields.io/badge/GitHub-Releases-3478b8?style=for-the-badge" alt="GitHub — versões da tradução"></a>
    <a href="https://discord.gg/uFEwSMwUYb"><img src="https://img.shields.io/badge/Discord-Vai_Brasil-5865f2?style=for-the-badge" alt="Discord — comunidade Vai Brasil"></a>
  </p>
  <p><a href="https://traducao.yuricunha.com/">Portal de Traduções</a> · <a href="#-instalação">Como instalar</a> · <a href="#-comunidade-e-suporte">Suporte</a></p>
</div>

## ⚔️ Um reino à beira do Flagelo

No reino de **Caelum**, contratos de sangue mantêm estradas abertas enquanto casas nobres disputam território e antigas ruínas guardam segredos de eras passadas. De décadas em décadas, porém, o **Flagelo** retorna com o despertar das fendas do Vazio, espalhando praga e criaturas por cinco regiões — de **Midland** e **Northern Hold** a **Westmire**, **Redlands** e **Dragate**.

Em *The Iron Oath*, você assume o comando de uma companhia mercenária em um RPG tático por turnos de fantasia sombria. Cada expedição cobra seu preço: semanas passam na estrada, recrutas envelhecem, acumulam ferimentos, mudam de lealdade ou tombam em calabouços sem retorno, enquanto decisões diplomáticas e escolhas em eventos moldam a reputação do grupo ao longo dos anos.

## 🇧🇷 O que a tradução cobre

O projeto traduz para o português brasileiro os textos voltados ao jogador nos recursos originais do jogo:

- **Campanha, diálogos e eventos:** prólogo, missões principais e secundárias, escolhas de diálogo, eventos de viagem, conselhos da guilda, rumores e conversas de acampamento.
- **Códice e lore de Caelum:** registros históricos das eras do mundo, famílias nobres, religiões, bestiário, habilidades inimigas, traços e condições de combate.
- **Interface, combate e gestão:** menus principais, telas de dificuldade, atributos, habilidades de classe, equipamentos, provisões, cidades, contratos e modificadores de masmorra.

As vozes e efeitos sonoros originais em inglês permanecem inalterados. Para evitar caracteres ausentes ou quadrados brancos nas fontes embutidas do jogo, o pacote final aplica a forma sem acentuação nos textos exibidos na maioria das telas de diálogo e menus.

## 🌐 Onde encontrar

- [GitHub Releases](https://github.com/isyuricunha/the-iron-oath-traducao-portugues-brasileiro/releases): pacote validado (`PTBR.zip`) a cada versão publicada.
- [Portal Vai Brasil!](https://traducao.yuricunha.com/): catálogo com os demais projetos e atualizações da comunidade.

## 📦 Download

Consulte a [página de Releases](https://github.com/isyuricunha/the-iron-oath-traducao-portugues-brasileiro/releases) e baixe **`PTBR.zip`** da versão compatível, quando disponível. Leia as notas da versão antes de instalar: elas informam a edição de referência do jogo, as categorias atualizadas e eventuais observações.

O pacote é publicado automaticamente somente após uma compilação completa e verificada. Os arquivos **Source code** listados pelo GitHub representam o código-fonte do repositório de trabalho, e não o pacote pronto para extrair no jogo. Verificação estrutural dos arquivos e teste visual em partida são etapas distintas; confira nas notas o alcance de cada atualização.

## 🛠️ Instalação

Como o jogo recebe a tradução diretamente nos arquivos de dados em `The Iron Oath_Data/`, faça sempre uma cópia de segurança antes de aplicar o pacote.

1. Feche completamente *The Iron Oath*.
2. Localize a pasta de instalação do jogo (na Steam: **Propriedades → Arquivos instalados → Explorar**; no GOG: pasta selecionada no GOG GALAXY ou no instalador).
3. Faça uma cópia de segurança da pasta `The Iron Oath_Data` em um local seguro fora da pasta do jogo.
4. Extraia o **conteúdo** de **`PTBR.zip`** diretamente dentro da pasta raiz de *The Iron Oath*, aceitando mesclar e substituir os arquivos existentes. A estrutura final deve manter os arquivos dentro de `The Iron Oath_Data/`:

   ```text
   The Iron Oath/
   └── The Iron Oath_Data/
       ├── level0 ... level6
       ├── resources.assets
       ├── sharedassets0.assets ... sharedassets6.assets
       ├── Managed/
       │   └── Assembly-CSharp.dll
       └── StreamingAssets/
           └── Data/
               ├── Codex/
               ├── Dialogue/
               ├── QuestData/
               └── ... demais subpastas em JSON e Yarn
   ```

5. Abra o jogo e confira o menu inicial, uma ficha de personagem e o códice para confirmar o carregamento dos textos em português.

Para atualizar para uma nova versão da tradução, feche o jogo e extraia o novo `PTBR.zip` sobre a pasta de instalação (ou restaure primeiro o backup original se o jogo base também tiver sido atualizado). Para remover a tradução, substitua `The Iron Oath_Data` pela sua cópia de segurança ou use a opção de **Verificar integridade dos arquivos** na Steam / **Verify / Repair** no GOG GALAXY.

## ⚙️ Compatibilidade e ajuda

A tradução toma como base os arquivos da versão **1.0.021** de *The Iron Oath* para Windows (PC). Atualizações futuras do jogo podem alterar textos, cenas ou bibliotecas compiladas e exigir uma nova compilação do pacote; evite misturar arquivos de versões diferentes.

<details>
<summary>Parte da tela apareceu em inglês ou falhou ao abrir após uma atualização do jogo</summary>

Verifique nas notas da Release se a versão instalada do jogo corresponde à versão de referência do pacote. Caso o jogo tenha sido atualizado recentemente, restaure os arquivos originais pela loja (Steam ou GOG) até que uma nova versão da tradução seja publicada. Também confira se o conteúdo de `PTBR.zip` foi extraído na pasta raiz do jogo sem criar uma pasta extra (como `PTBR/The Iron Oath_Data/`).

</details>

<details>
<summary>Texto cortado, concordância estranha ou palavra em inglês durante a partida</summary>

Abra uma Issue informando o contexto da tela (menu, nome do evento, habilidade ou entrada do códice), a versão do pacote instalada e, se possível, uma captura de tela. Não envie dados pessoais ao anexar registros ou saves de reprodução.

</details>

<details>
<summary>Os textos aparecem sem acentos nos diálogos e em vários menus</summary>

Esse comportamento é esperado no pacote final: a maioria das fontes embutidas no jogo original contém apenas o conjunto básico de caracteres ASCII sem diacríticos. Para evitar letras faltando ou símbolos substituídos em combate e diálogos, a compilação converte esses caracteres automaticamente para exibição limpa dentro do motor do jogo.

</details>

## 💬 Comunidade e suporte

- [Vai Brasil no Discord](https://discord.gg/uFEwSMwUYb): comunidade para tirar dúvidas, acompanhar atualizações e relatar erros.
- [Issues do projeto](https://github.com/isyuricunha/the-iron-oath-traducao-portugues-brasileiro/issues): relatos reproduzíveis, sugestões de termos e correções de texto.
- [Portal de Traduções](https://traducao.yuricunha.com/): outros projetos e novidades.

## 💚 Apoio

Se quiser apoiar voluntariamente a manutenção dos projetos, a chave Pix é **`pix@yuricunha.com`**. O apoio é totalmente opcional.

## 🤝 Créditos

- **Coordenação e manutenção:** [Yuri Cunha](https://github.com/isyuricunha).
- **Jogo:** desenvolvido por Curious Panda Games; publicação por Balor Games (e Humble Games nos créditos originais de lançamento), conforme a [página oficial na Steam](https://store.steampowered.com/app/699330/The_Iron_Oath/).

Projeto independente de tradução feito por fãs, sem vínculo oficial com os criadores ou publicadoras do jogo.

### 📜 Licença

O conteúdo original criado para este projeto de tradução, dentro dos direitos efetivamente detidos, segue a [CC BY-NC-SA 4.0](./LICENSE): atribua créditos, indique alterações, utilize sem fins comerciais e compartilhe adaptações sob a mesma licença ou outra compatível autorizada. [Resumo oficial da licença](https://creativecommons.org/licenses/by-nc-sa/4.0/).

Há uma [permissão adicional gratuita para integração oficial](./GAME-DEVELOPER-PERMISSION.md), inclusive em edições comerciais do jogo, mediante crédito e apenas para o material com direitos devidamente autorizados. As [diretrizes de contribuição](./CONTRIBUTING.md) exigem consentimento explícito para qualquer envio. Textos, nomes, imagens, marcas e arquivos originais do jogo permanecem sob titularidade de seus respectivos autores e publicadoras.

---

<p align="center"><em>Que nenhum juramento seja esquecido antes do próximo alvorecer em Caelum.</em></p>
