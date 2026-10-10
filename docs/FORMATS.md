# Referências dos formatos

Os leitores foram implementados em Python, usando somente a biblioteca padrão e preservando os bytes de metadados dos objetos. As estruturas foram conferidas com as implementações locais do cliente/servidor e estas referências primárias:

- [OTClient: ThingType](https://github.com/edubart/otclient/blob/master/src/client/thingtype.cpp): leitura de atributos, dimensões, padrões, grupos e referências de sprites.
- [OTClient: SpriteManager](https://github.com/edubart/otclient/blob/master/src/client/spritemanager.cpp): tabela de endereços e blocos comprimidos de sprites.
- [TFS: Items](https://github.com/otland/forgottenserver/blob/master/src/items.cpp): atributos OTB de Server ID e Client ID.
- [TFS: IOMap](https://github.com/otland/forgottenserver/blob/master/src/iomap.cpp): itens em tiles e nós de mapa.
- [Object Builder](https://github.com/ottools/ObjectBuilder): perfil OTFI e metadados 10.10+ com durações e grupos de animação.

Layout suportado: DAT com assinatura u32 e quatro limites u16 (itens começam em 100; demais categorias em 1); famílias de flags 8.60–9.86 e 10.10–10.98; Sprite IDs u16/u32; SPR com assinatura u32, quantidade u16/u32 conforme OTFI, tabela de offsets u32, color key de três bytes, tamanho u16 e runs RGB/RGBA. Durações e frame groups dependem dos booleans explícitos do perfil. Não se concatena nem renumera diretamente o DAT. Entradas sem conteúdo no SPR são normalizadas para sprite 0 durante a reconstrução.

OTBM 0 requer o OTB para interpretar o contador extra de stack no item compacto do tile. Nas versões 1–3 o ID compacto não carrega esse contador. A frequência conta objetos, não unidades dentro de stacks.

Limites preservados: Client ID continua u16; assinaturas DAT/SPR e OTFI de saída vêm do destino ou da origem em builds sem destino. A assinatura não comprova equivalência de conteúdo. Merge entre perfis diferentes é recusado. O output OTB contém definições de origem selecionadas e definições preservadas do destino, quando target_otb é fornecido.
