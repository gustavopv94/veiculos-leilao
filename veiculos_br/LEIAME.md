# Veículos em leilão — Brasil

Coleta veículos em leilão no Brasil todo e mostra num site local com busca e filtros
(condição/monta, ano, km, preço, UF/cidade, marca/modelo, tipo, origem, data do leilão, fonte).

## Instalação (uma vez)

```
pip install -r requirements.txt
python -m playwright install chromium     # navegador usado só para o Copart
```

## Uso

Rode da pasta `leiloes`:

```
python -m veiculos_br coletar              # todas as fontes (demora: abre a página de cada lote)
python -m veiculos_br coletar --rapido     # só as listagens (bem mais rápido, menos detalhes)
python -m veiculos_br coletar --fontes copart,vip
python -m veiculos_br servir --abrir       # site em http://127.0.0.1:8000
```

Os dados ficam em `veiculos.db` (SQLite). Cada coleta atualiza os lotes. Os que sumiram de uma fonte
que coletou sem erro saem do site (ficam inativos no banco).

## Fontes

| chave | site | observações |
|---|---|---|
| copart | copart.com.br | via Playwright (proteção anti-robô). Lotes "Em breve" ainda não têm data, preço nem monta |
| vip | vipleiloes.com.br | a busca do site aceita poucas requisições por minuto: a listagem leva cerca de 10 min |
| sodre | sodresantoro.com.br | API JSON |
| superbid, mega, franco, ljmg, gp, danielgarcia, bolsa | | as mesmas do `leiloes_mg.py`, sem o filtro de MG |
| detran, palacio | | só MG por natureza (Detran-MG, pátio de Juatuba/MG) |

## Condição (normalizada)

`Sucata` · `Grande monta` · `Média monta` · `Pequena monta` · `Sinistrado` (sinistro sem classificação de
monta) · `Conservado` · `Não informado`. Vem da classificação de monta da fonte, quando existe, ou de
palavras-chave nos detalhes, no título e na descrição. Confira sempre o edital.

## Favoritos e lembretes

- No site, clique no ♡ de um veículo para salvá-lo. O botão **♥ Favoritos** no topo mostra só eles.
- Ao abrir um veículo, escolha **🔔 Lembrar** (30 min, 1 h, 3 h, 12 h, 1 dia ou 2 dias antes) e, se quiser,
  anote algo (ex.: lance máximo).
- O aviso chega por **e-mail** e pela **notificação do Windows**. O e-mail sai de uma conta sua com uma
  **senha de app**. No Gmail: ative a verificação em duas etapas e crie a senha em
  myaccount.google.com/apppasswords. Configure no 🔔 do topo do site ou:
  ```
  python -m veiculos_br lembretes --email voce@gmail.com --senha-app "abcd efgh ijkl mnop" --para quem.recebe@exemplo.com
  ```
  A senha fica salva em `lembretes.json`, só no seu PC.
- Com o site aberto, os lembretes são conferidos a cada 5 min. Para receber mesmo com o site fechado
  (só precisa do PC ligado): `python -m veiculos_br lembretes --agendar`. Para desligar: `--desagendar`.
- O horário do leilão vem da última coleta. Rode `coletar` com frequência para pegar remarcações.

## Site na internet + coleta semanal

```
python -m veiculos_br senha SUA_SENHA          # obrigatório: protege o acesso pela internet (usuário: leilao)
python -m veiculos_br agendar                  # coleta toda segunda 08:00 + site sobe ao entrar no Windows
python -m veiculos_br agendar --dia sab --hora 10:00   # outro dia/horário
python -m veiculos_br agendar --remover        # desfaz as duas tarefas
python -m veiculos_br publicar                 # sobe site + link público agora (sem esperar o login)
```

- O link é gratuito, do Cloudflare Tunnel: `https://xxxx.trycloudflare.com`. Ele **muda quando o PC reinicia**.
  O link novo chega no seu e-mail, fica em `link_publico.txt` e aparece no topo do site quando aberto no PC.
- Pela internet, o navegador pede usuário (`leilao`) e senha. No próprio PC (127.0.0.1) não pede.
- Só funciona com o PC ligado e conectado. Se o PC estiver desligado no horário da coleta, ela roda assim que ele ligar.
- Registros: `coleta.log`, `site.log` e `lembretes.log` na pasta leiloes.
- Para um endereço fixo, é preciso um domínio próprio (ex.: registro.br, cerca de R$ 40/ano) ligado a uma conta
  gratuita da Cloudflare.
