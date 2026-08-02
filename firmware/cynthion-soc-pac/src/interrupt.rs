#[doc = r"Enumeration of all the interrupts."]
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
#[repr(u16)]
pub enum Interrupt {
    #[doc = "1 - CONSOLE"]
    CONSOLE = 1,
    #[doc = "2 - APOLLO_UART"]
    APOLLO_UART = 2,
    #[doc = "3 - BOARD_I2C"]
    BOARD_I2C = 3,
    #[doc = "4 - BOARD_I2C_MUX"]
    BOARD_I2C_MUX = 4,
}
#[doc = r" TryFromInterruptError"]
#[derive(Debug, Copy, Clone)]
pub struct TryFromInterruptError(());
impl Interrupt {
    #[doc = r" Attempt to convert a given value into an `Interrupt`"]
    #[inline]
    pub fn try_from(value: u8) -> Result<Self, TryFromInterruptError> {
        match value {
            1 => Ok(Interrupt::CONSOLE),
            2 => Ok(Interrupt::APOLLO_UART),
            3 => Ok(Interrupt::BOARD_I2C),
            4 => Ok(Interrupt::BOARD_I2C_MUX),
            _ => Err(TryFromInterruptError(())),
        }
    }
}
