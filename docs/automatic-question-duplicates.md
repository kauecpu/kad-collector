# Cópias de questões entre tipos de prova

Depois de cada coleta, o Collector agrupa automaticamente questões com o mesmo enunciado e as
mesmas alternativas, mesmo quando número, letra ou ordem mudam. Pequenos resíduos de extração
também são tolerados. O processo é local, não chama o Qwen e pode ser repetido sem trocar a cópia
principal quando os dados permanecem iguais.

A cópia principal é escolhida nesta ordem: revisão já aprovada, gabarito definitivo, vínculo de
gabarito válido, extração completa e menor quantidade de avisos. O último desempate usa a identidade
estável do documento e da questão.

Todas as cópias permanecem no SQLite como evidência. A fila normal e a exportação mostram apenas a
principal; os detalhes da questão listam os tipos, números e documentos preservados. Disciplina,
Matéria, Assunto e Nível preenchidos na principal são herdados pelas cópias. A resposta é relacionada
pelo texto da alternativa porque a letra pode mudar entre cadernos.

Se uma versão anterior deixar uma classificação humana ou do Qwen somente em uma cópia, a próxima
reclassificação recupera esse valor para a principal antes de aplicar regras locais. Regras podem
preencher campos vazios, mas não apagar valores válidos. A recuperação é registrada no histórico e
executá-la novamente sem mudanças não cria outro evento.

Alternativas realmente diferentes ou respostas oficiais incompatíveis não são fundidas. O caso fica
sinalizado para revisão sem impedir o processamento das outras questões. A ausência da mesma questão
em um dos tipos, por si só, não bloqueia classificação ou importação.
