# Módulo de importação — lado do computador

Recebe os CSV exportados pelo leitor DT50, valida e grava no banco, descartando
as leituras repetidas da mesma etiqueta dentro da mesma varredura (**RF06**).

Só biblioteca padrão do Python (3.9+). Não precisa instalar nada.

## Uso

```bash
cd inventario

python -m inventario salas planilha_de_salas.csv    # RF01, uma vez por unidade
python -m inventario bens relacao_patrimonial.csv   # RF02, antes de cada inventário
python -m inventario etiquetagem etiquetagem_*.csv  # RF03, durante a etiquetagem
python -m inventario importar /pasta/dos/csv        # RF06, a cada varredura
python -m inventario resumo                         # o que já está no banco
```

A ordem importa: sem as salas cadastradas, os bens não têm a que ambiente se
ligar, e o comando `bens` recusa rodar.

O banco fica em `dados/inventario.db` e os CSV originais são preservados em
`dados/brutos/` (RNF05). Ambos podem ser trocados por `--db` e `--brutos`.

Como tirar os arquivos do aparelho: eles ficam em `/sdcard/XLSFile/` no DT50.
Hoje a cópia é manual, por USB. O importador recebe um caminho de arquivo, então
trocar isso por um envio pela rede depois não mexe em nada aqui dentro.

## Cadastro do administrador

### Salas e tags de referência (RF01)

Uma linha por tag. A sala nasce da combinação unidade + nome, então não há
arquivo separado de salas:

```csv
unidade,sala,posicao,epc
Casa da Industria,Lab de Informatica 1,centro,E2801160REF0100
Casa da Industria,Lab de Informatica 1,canto_ne,E2801160REF0101
```

O modelo da seção 7.3 previa uma tag por sala; como são cinco (4 cantos +
centro), a tag virou tabela própria. O comando avisa sobre salas com menos de 5
tags — não é erro de cadastro, é etiquetagem incompleta.

**Essa planilha não precisa ser digitada.** A aba **Salas** do aplicativo gera um
arquivo com exatamente este cabeçalho: o operador lê a tag (ou o QR dela),
informa a sala, e o aparelho avança sozinho para a próxima posição. O arquivo
entra aqui sem conversão nenhuma — são 24 caracteres hexadecimais por tag, cinco
por sala, que ninguém precisa transcrever.

Duas recusas deliberadas: a mesma tag em duas salas, e duas tags na mesma
posição. Aceitar qualquer uma mandaria a varredura inteira para o ambiente
errado, e o erro só apareceria muito depois, na conciliação.

### Relação patrimonial (RF02)

```csv
patrimonio,descricao,sala esperada,metalico
10001,Microcomputador Dell,Lab de Informatica 1,nao
```

A planilha vem da FIEA, não do nosso aplicativo, então o cabeçalho é lido com
tolerância: acentos, maiúsculas, espaços e sinônimos são normalizados, e
separador `,` ou `;` é detectado sozinho. `N.º Patrimônio`, `TOMBO` e
`patrimonio` chegam todos ao mesmo campo. A coluna `metalico` é opcional e serve
ao planejamento das etiquetas on-metal (risco da seção 5.1).

**Linha ruim não derruba o arquivo.** As boas entram e as demais viram relatório
de inconsistências, como a seção 5.1 prevê:

```
3 linhas com problema:
  linha 7: 10006: sala 'Auditório' nao esta cadastrada (RF01)
  linha 8: patrimonio 10002 repetido (ja na linha 3)
  linha 9: sem numero de patrimonio
```

Um bem de sala desconhecida **entra assim mesmo**, sem vínculo, e é apontado:
cadastrar a sala depois e reimportar a relação conserta o vínculo.

**Reimportar a relação nunca apaga `epc`, `qr_code` nem `metalico`.** Esses campos
são fruto da campanha de etiquetagem (RF03) e custam voltar sala por sala para
refazer; uma planilha atualizada do patrimônio não pode destruí-los.

### Etiquetagem (RF03)

Liga o EPC da etiqueta ao número de patrimônio. O arquivo vem da aba
**Etiquetagem** do aplicativo, que grava cada par assim que ele é salvo:

```csv
timestamp,epc,patrimonio,metalico,origem_epc
2026-10-08 14:30:28.816,E2800BEM0001,10001,nao,rfid
2026-10-08 14:30:37.816,E2800BEM0003,10003,sim,manual
```

`origem_epc` diz se o EPC veio do rádio ou do QR impresso na etiqueta. A
distinção importa: etiqueta que não respondeu ao rádio na bancada provavelmente
também não vai responder na varredura, e o relatório conta esses casos à parte.

```
3 bens etiquetados, 0 reetiquetados
  1 tiveram o EPC capturado pelo QR, nao pelo radio

Etiquetagem: 3 de 4 bens (1 faltam)
  proximos sem etiqueta:
    10004  Projetor Epson  (Lab de Informatica 1)
```

Duas recusas: patrimônio fora da relação patrimonial, e a mesma etiqueta em dois
bens — aceitar a segunda faria dois bens disputarem a mesma leitura, e um deles
apareceria como não localizado para sempre. Trocar a etiqueta de um bem (quando a
anterior se danifica) é permitido, mas fica registrado no relatório em vez de
acontecer em silêncio.

**Não existe coluna `qr_code`.** O QR impresso carrega o próprio EPC, então
guardá-lo separado seria gravar o mesmo valor duas vezes. O RF03 do documento
ainda descreve "EPC e QR code" como se fossem dois dados distintos.

## O que a deduplicação faz

Uma varredura de 2 minutos gera centenas de linhas, porque cada etiqueta responde
muitas vezes. O RF06 colapsa isso em **uma linha por (varredura, etiqueta)**:

```
181 leituras brutas  ->  31 etiquetas  (150 repetidas descartadas)
```

As repetições não somem, viram evidência:

| campo | para que serve |
|---|---|
| `leituras` | quantas vezes a etiqueta respondeu — confiança da leitura |
| `rssi_min`, `rssi_medio`, `rssi_max` | intensidade do sinal |
| `primeira_leitura`, `ultima_leitura` | janela em que a etiqueta esteve no alcance |

Isso é deliberado. A conciliação (RF07/RF08) e a decisão de em qual sala cada
etiqueta está vão depender dessas estatísticas; se o importador jogasse as
repetições fora, essa informação só voltaria reimportando tudo.

**O escopo da deduplicação é a varredura, nunca a sessão.** A mesma etiqueta lida
na sala A e na sala B continua sendo dois registros — é disso que o RF08 precisa
para enxergar leitura cruzada.

## Estrutura

```
inventario/
  esquema.py      tabelas do modelo da seção 7.3
  planilha.py     leitura tolerante de .csv e .xlsx do administrador
  cadastro.py     salas e tags (RF01), relação patrimonial (RF02)
  etiquetagem.py  vínculo entre EPC e patrimônio (RF03)
  leitor_csv.py   parser, validação e agregação do RF06
  importador.py   grava no banco, arquiva o bruto, trata reimportação
  cli.py          linha de comando
testes/           python -m unittest discover -s testes
```

## Comportamentos que valem conhecer

- **Reimportar substitui.** O estado final é o mesmo tendo o arquivo entrado uma
  ou cinco vezes, e uma reexportação corrigida sobrepõe a anterior.
- **A ordem dos arquivos não importa.** O app só exporta `sessao_*.csv` no
  encerramento, então a varredura costuma chegar primeiro; o registro da sessão é
  aberto com unidade e operador que o próprio arquivo de leituras carrega, e
  completado depois com início, fim e duração.
- **Arquivo fora do formato é recusado com a linha do erro**, em vez de entrar
  pela metade.
- **Sessão com varredura faltando gera aviso.** Se o `sessao_*.csv` declara 5
  varreduras e só 4 foram importadas, o comando avisa — sem isso, uma sala
  esquecida na cópia viraria uma lista inteira de bens "não localizados".

## Planilhas .xlsx

O caminho testado é CSV, que sai da biblioteca padrão. Para ler `.xlsx`
diretamente, instale `openpyxl`; sem ele o comando diz isso em vez de estourar
um erro de importação. Salvar a planilha como CSV no Excel também resolve.

## Ainda não existe

Conciliação (RF07), leitura cruzada (RF08), lista de não localizados (RF09),
planilha final (RF12) e indicadores (RF13). O banco já tem tudo de que eles
precisam: salas com suas tags, bens com sua sala esperada e seu EPC, e leituras
deduplicadas por varredura.
