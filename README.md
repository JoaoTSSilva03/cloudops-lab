# CloudOps Lab

Um pequeno monitor de disponibilidade HTTP desenvolvido com Python, FastAPI, SQLite e Docker. Verifica um URL configurado e guarda o resultado e o tempo de resposta para consulta posterior.

## Funcionalidades atuais

- Verificação manual ou periódica de um serviço definido na configuração.
- Registo do código de estado HTTP, do tempo até à receção dos cabeçalhos, da data e hora em UTC e de eventuais falhas de rede.
- Consulta do histórico persistente, com as verificações mais recentes em primeiro lugar.
- Execução da API num contentor sem privilégios de administrador, acessível apenas através de `localhost`.
- Registos estruturados em JSON, com a origem e o resultado de cada verificação.

A monitorização automática começa no arranque da aplicação. Estão previstas a criação de painéis e a disponibilização da aplicação na AWS.

```mermaid
flowchart LR
    User[Utilizador / Swagger UI] --> API[FastAPI]
    Monitor[Monitor periódico] --> API
    API --> Target[Serviço HTTP configurado]
    API --> DB[(Histórico em SQLite)]
    API --> Logs[Registos JSON]
```

## Arranque com Docker

É necessário ter o Docker Desktop em execução, com contentores Linux, ou o Docker Engine com Compose num sistema Linux.

```sh
docker compose up --build -d
```

Abrir <http://localhost:8000/docs>. Na operação **POST /checks**, selecionar **Try it out** e, de seguida, **Execute**. Utilizar **GET /checks** para consultar o histórico e **GET /health** para confirmar que a API está ativa.

O URL predefinido é `https://example.com`. Para o alterar, copiar `.env.example` para `.env`, modificar `TARGET_URL` e executar novamente `docker compose up -d`. O serviço escolhido deve ser de confiança e a sua monitorização deve estar autorizada. O URL não deve conter credenciais nem tokens sensíveis.

Para acompanhar os registos da aplicação:

```sh
docker compose logs -f api
```

Para parar e remover os contentores:

```sh
docker compose down
```

O volume mantém o histórico após a execução de `down`. A opção `--volumes` elimina esse volume e os dados nele guardados.

## Verificações periódicas

A variável `CHECK_INTERVAL_SECONDS` define a pausa, em segundos, entre o fim de uma verificação automática e o início da seguinte. Aceita valores inteiros entre `0` e `86400`. O valor predefinido é `60`; `0` desativa apenas a monitorização automática, mantendo disponível a operação `POST /checks`.

Para experimentar com um intervalo de dez segundos, copiar `.env.example` para `.env` e definir:

```dotenv
TARGET_URL=https://example.com
CHECK_INTERVAL_SECONDS=10
```

Aplicar a configuração e reconstruir a imagem após atualizar o código:

```sh
docker compose up --build -d
```

Consultar `GET /checks` duas vezes, com cerca de dez segundos de intervalo. Devem surgir novos resultados sem executar `POST /checks`.

O monitor realiza a primeira verificação logo no arranque. Uma falha de ligação fica guardada como `down` e não interrompe o agendamento. Uma falha interna, por exemplo ao escrever na base de dados, gera um evento `check_failed` e uma nova tentativa no ciclo seguinte.

As verificações não se sobrepõem dentro do mesmo processo. Se existir uma verificação manual em curso, esse ciclo automático é adiado até à tentativa seguinte. Ao encerrar, o monitor interrompe a espera pelo próximo ciclo e aguarda a conclusão de uma verificação que já esteja em curso.

## Registos estruturados

Os eventos da aplicação são escritos na saída padrão, com um objeto JSON por linha. Os registos do servidor Uvicorn mantêm o formato habitual. Para os acompanhar sem o prefixo acrescentado pelo Compose:

```sh
docker compose logs --no-log-prefix -f api
```

Exemplo ilustrativo de um evento:

```json
{"timestamp":"2026-10-01T12:00:00+00:00","level":"INFO","event":"check_completed","check_id":1,"source":"scheduled","target_host":"example.com","status":"up","status_code":200,"latency_ms":125.4,"error":null}
```

| Campo ou evento | Significado |
| --- | --- |
| `source` | `manual` para pedidos à API; `scheduled` para verificações automáticas |
| `check_completed` | Verificação guardada; nível `INFO` se estiver `up`, ou `WARNING` se estiver `down` |
| `check_failed` | Falha interna que impediu a conclusão da verificação; nível `ERROR` |
| `monitor_started` / `monitor_stopped` | Arranque e encerramento do monitor |

Os registos incluem apenas o nome do host, sem o caminho, os parâmetros ou o fragmento do URL. As mensagens de exceção também não são registadas, para evitar expor dados sensíveis. O URL completo continua a fazer parte do histórico da API e da base de dados, pelo que não deve incluir segredos.

## Execução local com Python

É necessário Python 3.12 ou superior.

```sh
python -m venv .venv
```

Ativar o ambiente virtual com `.venv\Scripts\Activate.ps1` no PowerShell ou `source .venv/bin/activate` em Linux/macOS. De seguida, executar:

```sh
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

A base de dados SQLite é criada em `data/checks.db`. Na execução direta com Python, `TARGET_URL`, `CHECK_INTERVAL_SECONDS` e, opcionalmente, `DATABASE_PATH` devem ser definidos como variáveis de ambiente no terminal. O ficheiro `.env` só é carregado automaticamente pelo Compose.

## API

| Método | Caminho | Funcionamento |
| --- | --- | --- |
| GET | `/health` | Confirma que a API está ativa, independentemente da disponibilidade do serviço monitorizado |
| POST | `/checks` | Executa e guarda uma verificação; devolve HTTP 201 mesmo quando o serviço está indisponível |
| GET | `/checks?limit=20` | Devolve as verificações mais recentes, com um limite entre 1 e 100 |
| GET | `/docs` | Apresenta a documentação interativa da API |

As respostas HTTP de 200 a 399 são classificadas como `up`. Os restantes códigos de resposta finais e os erros de rede são classificados como `down`. Os redirecionamentos são registados, mas não são seguidos.

A latência mede o tempo até à receção dos cabeçalhos da resposta, incluindo a preparação do cliente HTTP. O corpo da resposta não é descarregado. O HTTPX tem um tempo limite de cinco segundos por operação de rede; este valor não corresponde a um limite total de cinco segundos para toda a verificação. A validação dos certificados TLS mantém-se ativa e as definições de proxy do ambiente são ignoradas.

## Testes

```sh
python -m pytest -q
```

Os testes utilizam respostas HTTP simuladas e bases de dados temporárias. Abrangem respostas de sucesso e de erro, redirecionamentos, tempos limite, erros de ligação, persistência após reinícios, ordenação do histórico e dados de entrada inválidos. Não é necessário aceder a um site público nem ter uma conta AWS.

Os testes do monitor verificam ainda a repetição automática, a recuperação após falhas, o encerramento, a desativação por configuração e o formato dos registos JSON.

O fluxo de integração contínua no GitHub Actions executa os testes, valida a configuração do Compose e constrói a imagem Docker a cada envio de alterações (*push*) e em pedidos de integração (*pull requests*). Também pode ser iniciado manualmente no separador **Actions**.

## Limitações e próximos passos

A API funciona localmente e ainda não tem autenticação nem limitação da frequência dos pedidos. Nesta fase, não deve ser exposta publicamente. O URL monitorizado é definido na configuração do ambiente, não através de pedidos à API. O histórico em SQLite ainda não tem uma política de retenção.

O agendamento funciona dentro do processo da API. Esta versão deve correr com **um único processo Uvicorn e uma única instância**: múltiplos processos ou réplicas criariam monitores independentes e verificações duplicadas. O monitor não recupera verificações perdidas enquanto a aplicação esteve parada. O encerramento aguarda a operação de rede em curso, sujeita aos tempos limite descritos acima. Os registos JSON ainda não têm uma solução dedicada de recolha e retenção.

1. Integrar a deteção de vulnerabilidades e de credenciais expostas no processo de integração contínua.
2. Criar a infraestrutura AWS com Terraform, após analisar os custos e os controlos de acesso.
3. Acrescentar métricas, alertas e um exercício documentado de recuperação de falhas.

Nesta fase, o repositório não cria recursos na AWS.

## Referências técnicas

- [Execução do FastAPI em contentores](https://fastapi.tiangolo.com/deployment/docker/)
- [Funcionamento dos tempos limite no HTTPX](https://www.python-httpx.org/advanced/timeouts/)
