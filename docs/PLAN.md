# Plano de consolidação dos assets

1. Identificar o mapa principal, o OTB exato do editor e os perfis binários das bases. Preservar todos os inputs.
2. Ler a árvore inteira do mapa e contar os Server IDs, incluindo ground compacto, house tiles e containers.
3. Resolver Server ID → Client ID pelo OTB e descobrir referências estáticas em Lua/XML/C++. Marcar dependências, expressões pendentes e conflitos de conteúdo. Bloquear dependências ausentes.
4. Gerar relatório consultável e manifesto de IDs reservados. Decidir entre manter os demais objetos do destino (`merge`) ou formar uma base inicial apenas com o mapa (`map-only`).
5. Revisar o relatório e usar o plano com hashes atuais para autorizar substituições. Copiar objetos completos para os mesmos Client IDs. Reservar lacunas, resolver expressões dinâmicas explicitamente e preservar mappings do OTB de destino quando fornecido.
6. Reconstruir o SPR usando os blocos efetivamente referenciados e deduplicar blocos iguais. Reescrever apenas os índices de sprites no DAT.
7. Reabrir os binários; comparar objetos/sprites e conferir mappings e integridade dos inputs. Persistir hashes e fingerprints para validação posterior independente. Publicar a saída separada apenas se esses checks passarem.
8. Abrir o mapa no RME e testar cliente/servidor. Consolidar itens dinâmicos, scripts e definições de gameplay antes de promover a base gerada a definitiva.

A ferramenta implementa as etapas 1–7. A etapa 8 precisa do editor/cliente e da configuração do servidor correspondente. O processo pode ser repetido, mantendo os manifests e usando uma saída anterior como destino da próxima importação.
