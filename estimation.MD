I) Tests de CircuitPython pour le respect des contraintes temporelles
 - Mise en place, application basique : 1/2 - 1 j
 - Communication sur bus CAN : 1/2 - 1 j
 - Tests des performances temporelles : 1 à 2 j
 - Conclusion : 1/2 j

I bis) (seulement si CircuitPython pas au point) Programmation en C++ de la communication CAN :
 - Setup projet et couche SPI : 1 - 2 j
 - Config MCP5625, et gestion par IRQ : 2 - 3 j
 - Gestion de la couche CAN : 2 - 3 j 

II) Conception et architecture globale du code et des modules :
 - Mise au point du protocole de communication entre module, MCU maitre et PC : 1 - 2 j

III) Assemblage de la beatbox :
 - analyse de chaque module pour identification des situations bloquantes (feeder alert) : 2 j
 - développement de remontée d'erreurs au MCU et au PC : 2 j
 - programmation des différents modules :
   - feeder : 1/2 j
   - capteur IR : 1j
   - plafonnier : 1j
   - ecran : 1j

 
IV) Tests et validation communication multi device : 2 - 3 j

V) Gestion de la communication avec le PC :
 - Communication série : 1/2 - 1 j
 - Gestion des commandes PC : 1 - 2 j
 - Envoi des messages des modules : 1 - 2 j

VI) Test, correction et robustification : 3 - 5 j

VII) Documentation : 2 -3

VIII) Mise à disposition code sur repo git : offert