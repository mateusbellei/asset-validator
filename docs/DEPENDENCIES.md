# Descoberta estática e resoluções

O scanner reconhece um conjunto explícito de referências. Não interpreta Lua/C++ arbitrário, não executa scripts, não resolve chamadas externas e não garante cobertura de APIs customizadas. A seleção pode ser uma aproximação conservadora: varrer uma pasta inteira inclui definições não usadas pelo mapa. Informe caminhos relevantes para limitar esse conjunto.

## Padrões reconhecidos

- Lua/C++: `lookType = ...`, `lookTypeEx = ...`, `effect = ...`, `magicEffect = ...`, `shootEffect = ...`, `distanceEffect = ...`.
- Itens: `createItem`, `CreateItem`, `addItem`, `transform`, `doCreateItem`, `doPlayerAddItem`.
- Visuais: `sendMagicEffect`, `sendDistanceEffect`, `doSendMagicEffect`, `doSendDistanceShoot`, `addMagicEffect`, `addDistanceEffect`.
- Combat: `setParameter(COMBAT_PARAM_EFFECT, ...)` e `setParameter(COMBAT_PARAM_DISTANCEEFFECT, ...)`.
- XML: `looktype`, `looktypeex`, `lookitem`; `<look type="...">`, `<mount type="...">`; IDs/ranges de `<item>`; atributos `decayTo`, `rotateTo`, `transformEquipTo`, `transformDeEquipTo`, `destroyTo`, `areaEffect`, `effect`, `shootEffect`, `distanceEffect`.
- Constantes em maiúsculas atribuídas a inteiros, aliases e expressões simples. Soma, subtração, OR e deslocamento limitado, sem `eval`.
- Tabelas C++ de nomes XML: `{"nome", CONST_ME_...}` e `{"nome", CONST_ANI_...}`, separadas por categoria. Inclua o arquivo dessas tabelas (por exemplo `server/src/tools.cpp`) e o arquivo dos enums no scan.

Comentários e strings de código são ignorados. Definições Lua de funções são excluídas dos padrões de chamada. XML não aceita DOCTYPE/entities. Constantes conflitantes e seus aliases ficam não resolvidos, salvo definição explícita em `scan.constants`.

Parâmetros, tabelas indexadas e cálculos dinâmicos não são adivinhados. Chamadas aninhadas que excedam os padrões ficam pendentes quando detectadas. APIs desconhecidas podem não ser detectadas; use `extra` e revise o escopo do scanner.

## Resoluções

O identificador de referência usa caminho absoluto, linha, categoria, espaço de IDs e expressão. `scan.resolutions` associa esse ID aos valores possíveis revisados. Itens do servidor usam Server IDs; outfits/effects/missiles usam Client IDs. Uma lista vazia exclui explicitamente aquela referência.

Considere todos os valores possíveis de expressões dinâmicas. Uma resolução incompleta não pode ser descoberta pela análise estática. Mudanças na localização ou expressão invalidam o ID; resoluções antigas não utilizadas bloqueiam a execução.

Lua/C++ usam UTF-8 por padrão. Para bases antigas, `scan.encoding` aceita `cp1252` ou `latin-1`. XML respeita sua declaração de encoding. Todos os arquivos lidos entram no snapshot de hashes do plano.

Enums sentinela e efeitos por tipo de arma precisam de revisão específica. Não trate um sentinela como sprite: resolva com os IDs possíveis ou lista vazia quando realmente não produzir visual.
