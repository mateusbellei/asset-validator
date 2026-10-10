# Asset Validator

Ferramenta para auditar e migrar assets OpenTibia a partir dos itens realmente usados em um mapa, preservando os IDs de servidor e cliente.

Requer Python 3.11 ou superior. Os arquivos originais são somente leitura; relatórios e bases geradas ficam em uma pasta nova.

## Estratégia

O mapa guarda **Server Item IDs**, e não índices de sprites. A ferramenta segue a cadeia:

`OTBM → Server ID → items.otb → Client ID → DAT → Sprite IDs → SPR`

Ela importa todos os itens utilizados no mapa: pisos, paredes, bordas, telhados, portas, decoração, obstáculos e itens dentro de containers. Filtrar apenas construções por aparência pode perder dependências necessárias. O mapa e os IDs do OTB continuam iguais. Os índices internos de sprites podem mudar porque todas as referências correspondentes no DAT são atualizadas.

Em `merge`, cada Client ID selecionado recebe o objeto completo da origem: flags, dimensões, padrões, animações e sprites. Os objetos do destino fora da seleção permanecem equivalentes. Quando o destino já tem um objeto diferente no mesmo ID, a origem vence: é necessário para preservar a aparência do mapa. O relatório explicita essas colisões.

Em `map-only`, são mantidos somente os itens do mapa e seleções adicionais explícitas. IDs intermediários continuam reservados como objetos vazios, sem deslocar os itens usados. As categorias não selecionadas ficam vazias. Esse modo cria uma base inicial enxuta para posterior composição; não é uma base completa de gameplay.

## Uso

Não há dependências externas em runtime. Execute na pasta da ferramenta:

```powershell
python -m asset_validator --help
python -m asset_validator audit --config local/config.json --output output/audit
python -m asset_validator build --config local/config.json --output output/migrated
python -m asset_validator validate output/migrated
```

Copie `examples/config.json` para `local/config.json` e ajuste os caminhos. Caminhos relativos são resolvidos a partir da pasta do JSON. `map` identifica o mapa principal; não use uma exportação parcial de itens como substituto. `otb` deve ser exatamente o OTB que o editor usava com a base de origem. `source` e `target` são pastas contendo `Tibia.dat`, `Tibia.spr` e `Tibia.otfi`.

As listas em `extra` selecionam **Client IDs da origem**, por categoria. Elas permitem incluir outfits, efeitos, missiles ou itens que não aparecem no mapa. Também preservam o mesmo ID e substituem a definição correspondente no destino. Para importar de outras bases, faça uma etapa por origem usando o resultado anterior como destino e mantenha o manifesto de IDs reservados.

Use sempre um caminho de saída novo e separado das pastas originais. Uma saída existente é recusada. O build trabalha em uma pasta temporária e publica o resultado apenas depois de todas as verificações; se falhar, remove somente essa pasta temporária.

## Saídas e marcação

| Arquivo | Conteúdo |
|---|---|
| `report.html` | Relatório local com busca por ID, filtros de status e colisões destacadas |
| `items.csv` | Server ID, Client ID, frequência, flags OTB, colisão e hashes de conteúdo |
| `report.json` | Relatório completo, hashes dos inputs, seleção, sprites necessários, contagens e validação |
| `reserved-ids.json` | IDs que devem permanecer reservados por categoria |
| `Tibia.dat`, `Tibia.spr`, `Tibia.otfi` | Base gerada no build |
| `items.otb` | Cópia exata do OTB da origem, preservando as ligações do mapa |
| `sprite-mapping.json` | Índice de sprite antigo → novo, separado por origem e destino |

O status `identical` compara metadados e os bytes dos sprites, independentemente do número de sprite nas duas bases. `replace` marca colisão, `new` indica ID ausente no destino e `missing-*` bloqueia o build. Contagens de status em `statuses` são por Server ID; `unique_client_statuses` conta Client IDs distintos, pois vários Server IDs podem compartilhar o mesmo Client ID.

## Validações e limites

- Leitura completa dos DATs, incluindo múltiplos grupos de animação, e rejeição de bytes excedentes.
- Validação da árvore OTB/OTBM, escapes, itens compactos, house tiles e containers aninhados. Tiles com atributos desconhecidos e encodings antigos não suportados são recusados.
- Detecção de IDs sem definição, IDs obsoletos utilizados, sprites fora do índice, offsets inválidos, blocos truncados e RLE RGBA inválido.
- Deduplicação dos blocos de sprites referenciados, seguida de reabertura dos binários gerados.
- Comparação dos metadados e conteúdo dos sprites de **todos** os objetos gerados com a origem esperada, incluindo objetos preservados do destino e entradas reservadas.
- Verificação da cópia do OTB e dos hashes dos arquivos originais antes e depois da execução.

Suporte atual: **DAT 10.98 estendido, sprites RGBA de 32×32, animações com durações e frame groups**, declarado explicitamente no OTFI. A versão comercial não é inferida pela assinatura; arquivos customizados sem esse perfil, RGB, sprite IDs de 16 bits, outros tamanhos e formatos protobuf são recusados ou não suportados. As referências de implementação estão em [docs/FORMATS.md](docs/FORMATS.md).

O resultado mantém a compatibilidade gráfica do mapa e a ligação fornecida pelo OTB. A cópia do OTB contém também itens da origem que não aparecem no mapa; ela **não** sincroniza automaticamente definições de gameplay nem mappings exclusivos da base de destino. Antes de uso definitivo, integre `items.xml`, scripts, transformações, decay/rotate IDs e itens criados dinamicamente. Inclua essas dependências em `extra` quando necessário. Não há varredura automática de Lua/XML/C++ nesta versão.

Abra uma cópia do mapa no RME com os quatro arquivos gerados e confira regiões, andares, portas e animações. Depois teste o cliente e o servidor com seus mappings/definições correspondentes. Essa inspeção visual não é substituída pelos checks binários.

## Desenvolvimento

```powershell
python -m unittest discover -s tests -v
python -m compileall -q asset_validator
```

O CI executa os testes em Windows e Linux, com Python 3.11, 3.12 e 3.13. Os fixtures são sintéticos: nenhum DAT/SPR, mapa, relatório privado ou caminho local é publicado. `local/` e `output/` são ignorados pelo Git.

O plano de execução está em [docs/PLAN.md](docs/PLAN.md).
