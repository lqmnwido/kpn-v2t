package com.example.kpn;

import java.util.List;
import java.util.stream.IntStream;

final class ReportGuidance {
    private static final List<String> PURPOSES = List.of(
            "Nyatakan topik, tujuan dan skop sebenar pembentangan. Huraikan kaitan antara tema utama. "
                    + "Tinggalkan pengenalan ahli panel, jadual acara dan jemputan untuk bertanya.",
            "Kenal pasti masalah atau jurang yang dibangkitkan, pihak yang terkesan, "
                    + "serta punca dan akibat yang diterangkan. Tinggalkan peralihan antara sesi dan pertanyaan umum.",
            "Huraikan hujah, dapatan, mekanisme dan contoh utama. Terangkan hubungan sebab dan kesan, "
                    + "perbandingan atau batasan yang membantu memahami isi.",
            "Huraikan halangan yang menyukarkan pelaksanaan, penguatkuasaan atau penyertaan. "
                    + "Jelaskan bagaimana halangan itu menjejaskan pihak yang terlibat.",
            "Terangkan arah tuju atau keutamaan jangka panjang yang dibincangkan, "
                    + "termasuk bentuk kerjasama dan perubahan yang diperlukan. Bezakan daripada cadangan khusus.",
            "Catat tindakan khusus yang benar-benar dicadangkan. Nyatakan pelaksana dan tujuan jika jelas. "
                    + "Bezakan cadangan, langkah yang masih dipertimbangkan dan tindakan yang telah dilaksanakan.",
            "Kenal pasti soalan substantif peserta. Padankan jawapan atau maklum balas "
                    + "hanya apabila kaitannya jelas. Tinggalkan ucapan moderator dan jangan reka jawapan.",
            "Satukan dapatan utama dan implikasinya terhadap pembinaan negara bangsa. "
                    + "Jelaskan hubungan antara isu, langkah dan kesannya tanpa mengulang semua bahagian atau menambah cadangan.");

    private static final List<String> HEADINGS = ReportTemplate.numberedHeadings();

    private ReportGuidance() {}

    static String forSection(int number) {
        return HEADINGS.get(number - 1) + "\n" + PURPOSES.get(number - 1);
    }

    static String allSections() {
        return String.join("\n\n", IntStream.rangeClosed(1, HEADINGS.size())
                .mapToObj(ReportGuidance::forSection).toList());
    }
}
