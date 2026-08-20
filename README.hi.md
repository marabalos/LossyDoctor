# LossyDoctor

[English](README.md) | [Español](README.es.md) | [中文](README.zh-CN.md) | [Русский](README.ru.md) | [हिन्दी](README.hi.md)

---

## पहले: *lossy* का अर्थ क्या है

MP3, AAC, Vorbis, Opus और अन्य *lossy* फ़ॉर्मैट फ़ाइल का आकार घटाने के लिए ऑडियो की कुछ जानकारी को अपरिवर्तनीय रूप से हटा देते हैं।

इसी कारण **किसी lossy फ़ाइल को कभी भी master source, preservation format या interchange format के रूप में उपयोग नहीं करना चाहिए**। उसे किसी दूसरे lossy फ़ॉर्मैट में दोबारा encode करने से केवल एक और पीढ़ी की गुणवत्ता-हानि जुड़ती है। उसे lossless फ़ॉर्मैट में बदलने से केवल फ़ाइल का आकार बढ़ता है; पहले से खोया हुआ डेटा वापस नहीं आता।

फिर भी बहुत बड़ी मात्रा में संगीत, रिकॉर्डिंग, प्रसारण, bootleg, ऐतिहासिक फ़ाइलें और डिजिटल रूप से वितरित सामग्री **या तो केवल lossy फ़ॉर्मैट में मौजूद है, या व्यवहार में केवल उन्हीं फ़ॉर्मैट में प्रसारित होती है**।

LossyDoctor इन्हीं परिस्थितियों के लिए बनाया गया है।

**यह lossy संग्रहों के लिए बनाया गया उपकरण है, लेकिन इसकी सोच audiophile जैसी और कार्य-पद्धति रूढ़िवादी है: जो प्रामाणिक सामग्री अभी भी मौजूद है, उसे अधिकतम सीमा तक सुरक्षित रखना—सिर्फ “repair” करने के लिए उसे फिर से degrade किए बिना।**

## LossyDoctor क्या है?

LossyDoctor lossy ऑडियो फ़ाइलों का audit करता है और corruption, structural anomalies, bitstream समस्याएँ, timeline समस्याएँ और decoding failures खोजता है।

इसका मूल सिद्धांत सरल है:

> **कभी भी lossy re-encoding नहीं—repair के लिए भी नहीं।**

यदि फ़ाइल को उसके मूल compressed audio को सुरक्षित रखते हुए ठीक किया जा सकता है, तो LossyDoctor एक repaired copy बनाता है और फिर उसे verify करता है।

यदि यह संभव नहीं है, लेकिन वास्तविक रूप से recover किए जा सकने वाले PCM को ठीक-ठीक सिद्ध किया जा सकता है, तो LossyDoctor उसे **lossless FLAC** के रूप में सुरक्षित कर सकता है। परिणामस्वरूप फ़ाइल बड़ी होगी, लेकिन उसमें कोई नई हानि नहीं जोड़ी जाएगी: वह ठीक वही recovered PCM सुरक्षित रखेगी और ऐसे ऑडियो को playable बना सकती है जो अन्यथा playable नहीं रहता।

मूल फ़ाइल हमेशा अपरिवर्तित रहती है।

## यह क्या करता है

- केवल extension पर नहीं, बल्कि फ़ाइल की वास्तविक सामग्री के आधार पर format पहचानता है।
- structure, bitstream, timeline और decoding का audit करता है।
- ऐसी corrupted फ़ाइलें भी खोज सकता है जो अभी भी चलती हों।
- केवल तभी repair करता है जब एक demonstrable correction मौजूद हो।
- मूल compressed bitstream को हमेशा सुरक्षित रखता है।
- प्रत्येक repaired फ़ाइल को फिर से verify करता है।
- जब मूल फ़ाइल की सुरक्षित repair संभव न हो, तो सिद्ध रूप से genuine recoverable PCM को FLAC में सुरक्षित कर सकता है।
- एकल फ़ाइलों या पूरे संग्रहों को process कर सकता है।
- source फ़ाइल को कभी modify या overwrite नहीं करता।

संस्करण 1.1.0 प्रत्येक format family के लिए सिद्ध authority की सीमा के भीतर MPEG Layer II/III, AAC/ADTS, single-track MP4/AAC, Ogg/Opus, Ogg/Vorbis और ASF/WMA को कवर करता है।

## यह क्या नहीं करता

- **MP3 को MP3 में, AAC को AAC में दोबारा encode नहीं करता और किसी भी repair के लिए नई lossy compression का उपयोग नहीं करता।**
- मूल encoding के दौरान खो चुकी ध्वनि-गुणवत्ता को वापस नहीं लाता।
- ऐसे ऑडियो को गढ़ता या पुनर्निर्मित नहीं करता जिसका मूल अस्तित्व सिद्ध न किया जा सके।
- केवल इसलिए फ़ाइल को स्वस्थ नहीं मानता कि कोई decoder उसे चला सकता है।
- हर प्रकार की corruption को repair करने का दावा नहीं करता।
- हर problematic फ़ाइल को स्वतः FLAC में नहीं बदलता: lossless recovery केवल तब preservation विकल्प है जब मूल bitstream को बिना re-encoding के सही रूप से सुरक्षित रखना संभव न हो।

## यह किस प्रकार की समस्याएँ खोज सकता है?

किसी फ़ाइल में अभी भी recoverable audio मौजूद हो सकता है, फिर भी उसमें glitches, अधूरा playback, गलत duration, seeking की समस्या या कुछ players में पूरी तरह unreadable होने जैसी समस्याएँ हो सकती हैं।

LossyDoctor जिन समस्याओं का पता लगा सकता है, उनमें शामिल हैं:

- truncated MPEG frames या synchronization loss;
- असंगत headers या Xing/Info अथवा VBRI indexes;
- अनपेक्षित bytes या गलत padding;
- CRC errors;
- corrupted या out-of-sequence Ogg pages;
- timestamp या continuity समस्याएँ;
- MP4/AAC में गलत tables, offsets या durations;
- असंगत ADTS headers;
- अधूरे ASF/WMA packets या fragments.

**किसी समस्या का पता लग जाना अपने-आप यह नहीं दर्शाता कि उसे repair किया जा सकता है।**

जब एक ही सुरक्षित repair मौजूद हो, LossyDoctor उसे लागू कर सकता है। जब bitstream को सुरक्षित नहीं रखा जा सकता लेकिन genuine PCM को सिद्ध किया जा सकता है, तो वह बिना किसी नई पीढ़ी की हानि जोड़े उस ऑडियो को recover कर सकता है। जब दोनों में से कुछ भी सिद्ध नहीं किया जा सकता, तो वह damage report करता है और कोई काल्पनिक solution नहीं बनाता।

## उपयोग के उदाहरण

**पुराने संग्रह**  
वर्षों में अलग-अलग स्रोतों से एकत्र हुए हजारों MP3, AAC, WMA, Vorbis या Opus फ़ाइलों का audit करना।

**Glitches वाली फ़ाइलें**  
यह निर्धारित करना कि skip, dropout या playback failure किसी repairable structural anomaly से आया है या ऑडियो वास्तव में खो चुका है।

**वे फ़ाइलें जो अब सही तरह से नहीं चलतीं**  
जहाँ demonstrable repair उपलब्ध हो वहाँ मूल bitstream को सुरक्षित रखने की कोशिश करना; या अत्यंत खराब मामलों में उस genuine PCM को बचाना जिसे अभी भी निश्चित रूप से स्थापित किया जा सकता है।

**Preservation**  
Lossy सामग्री को स्थायी संग्रह में जोड़ने से पहले verify करना, बिना उसे एक और lossy compression generation से degrade किए।

## त्वरित तुलना

| उपकरण | मुख्य ताकत | LossyDoctor की तुलना में |
| --- | --- | --- |
| **LossyDoctor** | Audit + conservative repair + lossless recovery + post-repair verification | कम formats कवर करता है; केवल स्पष्ट रूप से समर्थित और सिद्ध मामलों में हस्तक्षेप करता है |
| **MP3val** | बहुत तेज़, सरल और MPEG structure के लिए विशेषीकृत | formats और decoding/PCM evidence दोनों में कहीं अधिक सीमित |
| **foobar2000 File Integrity Verifier** | decoding errors खोजने के लिए सुविधाजनक और व्यापक format compatibility | अधिकांश formats में detection मुख्यतः उन errors पर केंद्रित है जो decoding रोक देते हैं; यह समान repair-and-preservation system नहीं है |

LossyDoctor का लक्ष्य वह repair tool बनना नहीं है जो सबसे अधिक फ़ाइलों को बदल दे।

इसका लक्ष्य अलग है:

> **Lossy फ़ाइल में जो कुछ भी अभी प्रामाणिक रूप से मौजूद है, उसे बिना किसी नई पीढ़ी की हानि जोड़े सुरक्षित रखना। Repair तभी करना जब repair को सिद्ध किया जा सके। Lossless recovery तभी करना जब वही एकमात्र सुरक्षित विकल्प हो। और जिस चीज़ को निश्चित रूप से निर्धारित नहीं किया जा सकता, उसे न छूना।**
