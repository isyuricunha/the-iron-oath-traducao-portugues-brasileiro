# Contribuições

A participação neste projeto é voluntária. Preserve os identificadores, textos originais e marcadores técnicos (`{0}`, `[FNameP0]`, `<color=...>`, `<sprite name="...">`, `|NEXTPAGE|`, etc.). Consulte [translation-work/README.md](translation-work/README.md) antes de trabalhar nos lotes.

## Regras de autoria e consentimento explícito

Relatos de bugs, sugestões pontuais de termos e revisão puramente editorial não transferem autoria automaticamente, mas linhas originais traduzidas ou documentação inédita podem ser protegidas por direito autoral. Este repositório **não presume consentimento** a partir de um envio sem licença especificada, de uma issue aberta ou de colaboração informal.

Contribuições de tradução ou documentação com relevância autoral só podem ser incorporadas após **autorização afirmativa e auditável** registrada no Pull Request (ou em registro equivalente assinado para contribuições recebidas por outro canal). O autor mantém a titularidade de seu trabalho e declara deter os direitos necessários para conceder a permissão abaixo.

Inclua e marque pessoalmente esta declaração no Pull Request:

- [ ] Li `CONTRIBUTING.md` e `GAME-DEVELOPER-PERMISSION.md`. Autorizo de forma não exclusiva e gratuita o uso, edição e redistribuição das minhas contribuições originais deste PR sob CC BY-NC-SA 4.0 e a concessão da permissão adicional de integração oficial comercial à desenvolvedora/editora original de *The Iron Oath*, com créditos, sem pagamentos ou novas aprovações individuais. Declaro deter os direitos necessários.

Registre também `translation-work/contributor-consents/PR-NUMERO.json` contendo:

- `identity`: login ou identificador verificável do colaborador
- `pull_request`: URL do PR ou referência equivalente auditável
- `scope`: `"original contributions in this pull request"`
- `permission`: `"CC-BY-NC-SA-4.0 + official-game-commercial-integration"`
- `affirmative`: `true`

A manutenção conferirá identidade, escopo, créditos desejados e consentimento antes de integrar o PR. O verificador [translation-work/consent.py](translation-work/consent.py) valida a caixa marcada e o registro de consentimento usando o evento do PR.

Contribuições sem consentimento explícito não serão mescladas ao pacote autorizado para integração oficial (ou permanecerão explicitamente excluídas dessa permissão caso não sejam autorizadas). Não envie recursos proprietários adicionais do jogo, credenciais ou material de terceiros sem licença compatível.
