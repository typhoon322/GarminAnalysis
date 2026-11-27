package com.garminanalysis.app

import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

/** Shanghai timestamps for a user who lives in Asia/Shanghai. */
object Clock {
    val SHANGHAI: ZoneId = ZoneId.of("Asia/Shanghai")

    private val ISO_SECONDS: DateTimeFormatter =
        DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssXXX")

    fun nowSh(): ZonedDateTime = ZonedDateTime.now(SHANGHAI)

    fun todaySh(): LocalDate = nowSh().toLocalDate()

    fun isoSh(moment: ZonedDateTime? = null): String {
        val current = moment ?: nowSh()
        return current.withZoneSameInstant(SHANGHAI).format(ISO_SECONDS)
    }
}
