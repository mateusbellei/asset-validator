# Asset Validator

Auditoria e migração seletiva de assets OpenTibia, preservando IDs dos mapas. Python 3.11+, sem dependências externas em runtime. Originais somente leitura; cada execução publica uma pasta nova depois da validação.

## Auditoria sem destino

São necessários `source` (pasta DAT/SPR/OTFI) e `otb` correspondente. `map`, `maps`, `scan` e `extra` são opcionais. `target` é necessário apenas para comparar colisões ou preservar uma base existente no merge. Os caminhos relativos são resolvidos a partir do JSON.

```json
{
  "source": "source",
  "otb": "source/items.otb",
  "map": "maps/world.otbm",
  "scan": {"paths": ["server/data", "server/src/const.h"], "constants": {}, "resolutions": {}},
  "extra": {"items": [], "outfits": [], "effects": [], "missiles": []}
}
```

Use `maps: ["maps/one.otbm", "maps/two.otbm"]` no lugar de `map` para auditar vários mapas. Sem mapa, a ferramenta valida a base e audita seleções e scripts. O mapa usa Server IDs; o OTB os liga a Client IDs do DAT, que referenciam Sprite IDs do SPR.

```powershell
python -m asset_validator audit --config local/config.json --output output/audit
python -m asset_validator check-inputs --config local/config.json
```

Ambos verificam todos os objetos DAT, todos os sprites (inclusive não utilizados), correspondência OTB/DAT, mapas e dependências configuradas. `audit` também exporta relatórios. O OTB precisa pertencer à base usada pelo editor; igualdade de versão não prova igualdade de mappings.

## Dependências além do mapa

`scan.paths` percorre Lua, XML, H, HPP e CPP. A ferramenta não executa código. Reconhece padrões documentados de criação/transformação de itens, outfits, efeitos, missiles e transformações XML. Server IDs descobertos são convertidos pelo OTB; outfits/efeitos/missiles são Client IDs. `extra` usa Client IDs da origem.

Constantes numéricas e aliases podem ser resolvidos por declarações encontradas ou `scan.constants`. Tabelas C++ como `{"blueshimmer", CONST_ME_MAGIC_BLUE}` resolvem nomes XML quando seus arquivos e enums entram em `scan.paths`. Nomes conflitantes permanecem não resolvidos. Referências calculadas e nomes desconhecidos aparecem no relatório com `reference_id`, arquivo, linha e expressão. **Essas pendências bloqueiam builds.**

Para resolver uma pendência, informe os valores possíveis daquele ponto em `scan.resolutions` e gere nova auditoria:

```json
{"scan": {"paths": ["server/data"], "resolutions": {"reference-id-do-relatorio": [253, 254]}}}
```

Os valores são Server IDs para referências de itens do servidor, e Client IDs nas demais categorias. Uma lista vazia exclui explicitamente aquela referência. Resoluções antigas que não correspondem mais ao código são recusadas. Zero representa ausência de visual/remoção. Veja [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) para padrões e limites.

## Merge com revisão explícita

Adicione `target`, `mode: "merge"` e, quando disponível, `target_otb`. Os objetos selecionados mantêm os Client IDs e recebem os metadados e sprites da origem. Objetos fora da seleção permanecem equivalentes ao destino; o mapa conserva os Server IDs.

**A política padrão `conflicts: "error"` bloqueia substituições.** Para permitir uma substituição, configure `conflicts: "replace-reviewed"`, gere e revise a auditoria, e passe seu `report.json` ao build:

```powershell
python -m asset_validator audit --config local/merge.json --output output/merge-plan
python -m asset_validator build --config local/merge.json --plan output/merge-plan/report.json --output output/merged
python -m asset_validator validate output/merged --inputs
```

O plano precisa corresponder à configuração, seleção, dependências, objetos e hashes dos inputs atuais. Alterações exigem nova auditoria. Referências ausentes, mappings incompatíveis e perfis diferentes bloqueiam o build mesmo com plano.

Com `target_otb`, são preservadas definições completas exclusivas do destino e importadas definições selecionadas da origem. Divergências de flags/metadados OTB exigem revisão. Um mesmo Server ID ligado a Client IDs diferentes é incompatível com a preservação dos IDs e bloqueia o build. Versões OTB precisam ser compatíveis.

Sem `target_otb`, a saída contém apenas mappings da origem ligados aos Client IDs selecionados. O relatório informa que mappings exclusivos do destino não foram verificados. A ferramenta não reescreve `items.xml` nem a lógica dos scripts.

## Base enxuta

`mode: "map-only"` cria uma base com o mapa e seleções adicionais, sem precisar de destino. IDs intermediários ficam reservados como objetos vazios. Categorias não selecionadas ficam vazias. O OTB de saída contém mappings selecionados, evitando referências a itens removidos.

```powershell
python -m asset_validator build --config local/source-only.json --output output/map-base
python -m asset_validator validate output/map-base --inputs
```

Essa é uma base inicial para composição posterior. Configure scripts, criaturas e itens dinâmicos antes de considerá-la completa para gameplay.

## Verificação da entrada e da saída

Os checks incluem árvores/escapes OTB/OTBM, objetos DAT, animações, índices/offsets SPR, RLE e sobreposição de blocos. Todo sprite de entrada é verificado. O build deduplica blocos referenciados e atualiza índices internos no DAT sem renumerar objetos.

O build reabre os binários e compara metadados e conteúdo dos sprites de cada objeto com a origem esperada, incluindo objetos preservados e lacunas. Mappings do mapa e hashes dos inputs são conferidos novamente.

`validate DIRECTORY` verifica toda a base e, para builds v2, hashes do manifesto, fingerprints persistidos e mappings de dependências. `--inputs` confere também os hashes dos originais. Para bases externas sem manifesto, realiza validação estrutural e declara `manifest_verified: false`. Hashes detectam corrupção; não são assinatura de autenticidade contra alteração conjunta dos arquivos e relatórios.

Saídas existentes ou dentro das bases originais são recusadas. Falhas removem somente a pasta temporária da ferramenta. A validação binária não substitui abrir uma cópia do mapa no RME e testar cliente/servidor.

## Perfis suportados

| Componente | Suporte |
|---|---|
| Mapas | OTBM 0–3; v0 usa flags OTB para contadores de stacks compactos |
| DAT | Famílias de atributos 8.60–9.86 e 10.10–10.98, com perfil explícito |
| SPR | IDs de 16/32 bits, RGB/RGBA |
| Sprites | 32, 64 ou 128 pixels por lado, declarados no OTFI |
| Animações | Com/sem durações e frame groups, conforme perfil |

Informe `client-version` no `Tibia.otfi` para formatos diferentes de 1098; ausência mantém 1098 por compatibilidade com v0.1. Booleans `extended`, `transparency`, `frame-durations` e `frame-groups` devem ser explícitos. `sprite-data-size` descreve o buffer RGBA decodificado: lado² × 4, mesmo para compressão RGB.

Origem e destino precisam ter o mesmo perfil. A versão não é inferida pela assinatura. Protobuf, versões anteriores a 860, 987–1009, posteriores a 1098, flags customizadas não suportadas e conversão entre perfis ficam fora do escopo. Perfis adicionais têm testes sintéticos; integração com bases reais foi validada em 1098.

## Saídas

| Arquivo | Conteúdo |
|---|---|
| `report.html` | Busca por ID/arquivo, filtros, colisões e pendências |
| `items.csv` | Server ID, Client ID e frequência no mapa |
| `assets.csv` | Seleção de itens/outfits/efeitos/missiles e conflitos |
| `dependencies.csv` | Referências de código/XML, localização e resolução |
| `report.json` | Plano, perfis, validações, hashes, erros e avisos |
| `reserved-ids.json` | IDs reservados por categoria |
| `Tibia.dat`, `Tibia.spr`, `Tibia.otfi`, `items.otb` | Base gerada |
| `sprite-mapping.json` | Sprite ID antigo → novo, por origem/destino |
| `expected-records.json` | Fingerprints para validação independente posterior |

Erros fazem audit/check-inputs retornar 1; falhas de leitura/build retornam 2. O console mostra até 20 erros/avisos e suas contagens; relatórios mantêm a lista completa. `statuses` conta Server IDs; `unique_client_statuses` conta Client IDs distintos.

## Desenvolvimento

```powershell
python -m unittest discover -s tests -v
python -m compileall -q asset_validator
```

CI: Windows/Linux, Python 3.11/3.12/3.13. Fixtures sintéticos. `local/`, `output/` e binários proprietários são ignorados pelo Git. Referências: [docs/FORMATS.md](docs/FORMATS.md).
