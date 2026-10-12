# Controle Remoto Universal

Servidor Python + interface web para o celular. Controla TVs smart pelo Wi-Fi e TVs antigas
(sem Wi-Fi) por um emissor infravermelho barato.

## Iniciar
- **Windows:** dê dois cliques em `run.bat`
- **Linux / Mac / Raspberry Pi / Termux (Android):** `./run.sh`

Abra no celular o endereço mostrado (ex.: `http://192.168.0.10:5000`), **no mesmo Wi-Fi da TV**.
Dica: no navegador, "Adicionar à tela inicial". Mantenha o programa rodando num PC/Raspberry ligado.

## Primeiro uso
Toque em **＋ > Procurar na rede**, escolha a TV, **Salvar**. O app testa a conexão; se a TV pedir,
aceite na tela dela.

## Telas
- **Controle:** setas, OK, volume, canal, mudo, fonte, números, play/pause.
- **Apps:** lista os apps da TV (Netflix, YouTube, Prime, Disney+, Globoplay...) e abre com um toque.
- **Teclado:** digite no celular e o texto vai para a busca/campo da TV.
- **⚙ Gerenciar:** testar, editar IP, **resetar a conexão**, remover TV, aprender teclas IR,
  diagnóstico e **apagar tudo e recomeçar do zero**.

## Configurar cada tipo
| Tipo | O que fazer na TV |
|---|---|
| Samsung (2016+) | Aceite "Permitir" na 1ª conexão. Se negou: Configurações > Geral > Gerenciador de dispositivos externos |
| LG webOS | Ligar "LG Connect Apps" e aceitar o pedido na 1ª conexão |
| Roku / TV Roku | Configurações > Sistema > Config. avançadas > Controle por apps móveis > Permissivo |
| Android TV / Google TV | Salvar, depois **Parear**: toque "Mostrar código" e digite o código da TV |
| Sony Bravia | Rede doméstica > Controle IP: autenticação "Normal e Pre-Shared Key", defina a chave e informe no app |

## TV antiga / qualquer TV: emissor infravermelho
1. **Broadlink RM mini/pro** (mais fácil, ~R$ 80-150): configure no app oficial, depois adicione como
   "IR (Broadlink)". Já traz códigos prontos de **LG, Samsung e Sony** e **aprende qualquer outro
   controle** (⚙ > Aprender tecla).
2. **ESP8266/ESP32 + LED IR com Tasmota** (~R$ 30, DIY): adicione como "IR (Tasmota)". Códigos prontos
   de LG, Samsung e Sony; outras marcas via `ir_codes` em `data/devices.json`, ex.:
   `"ir_codes": {"power": "NEC:0x00FF02FD"}`.
Aponte o emissor para a TV. TV por IR não abre apps nem digita texto (limitação do infravermelho).

## Se algo não funcionar
1. ⚙ > **Testar**. A mensagem diz o que fazer.
2. IP mudou? ⚙ > **Editar IP** (use IP fixo/reserva DHCP no roteador para não mudar mais).
3. ⚙ > **Resetar conexão** e refaça o pareamento.
4. Ainda nada: ⚙ > Diagnóstico mostra o erro técnico; ou **Apagar tudo** e comece do zero.
5. Celular e PC em redes diferentes (Wi-Fi visitante, 5G) não funcionam.

## Limites
- Samsung anterior a 2016 e alguns modelos muito novos podem não funcionar por Wi-Fi: use IR.
- Ligar TV desligada: informe o MAC (Wake-on-LAN) ou use IR; depende do modelo.
- Algumas teclas (canal, menu...) não existem em todos os modelos.
